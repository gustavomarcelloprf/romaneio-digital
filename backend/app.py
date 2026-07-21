# --- Bibliotecas Padrão do Python ---
import os
import re
import io
import hmac
import functools
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
sys.path.append(str(Path(__file__).resolve().parent))

# --- Bibliotecas de Terceiros (Flask, etc.) ---
from flask import (
    Flask, request, jsonify, send_from_directory, send_file,
    session, abort, redirect, url_for, render_template
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- Módulos do Projeto (Romaneio Digital) ---
from database import (
    init_db, get_conn, salvar_pix_by_codigo,
    get_pedido, get_itens_pedido, get_loja_by_codigo,
    get_usuario, get_usuario_by_id
)
from services.render_png import render_pedido_png
from services.render_pdf import render_pedido_pdf

# -----------------------------------------------------------------------------
# Configuração do App e Paths
# -----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONT_DIR = PROJECT_ROOT / "frontend"
HTML_DIR = FRONT_DIR / "html"

app = Flask(__name__, template_folder='templates')
app.config.update(
    JSON_AS_ASCII=False,
    SECRET_KEY=os.environ["SECRET_KEY"],
    TEMPLATES_AUTO_RELOAD=True,
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

if not os.environ.get("ADMIN_TOKEN"):
    raise RuntimeError("ADMIN_TOKEN não configurado.")

init_db()
# -----------------------------------------------------------------------------
# Funções Helper
# -----------------------------------------------------------------------------
def _ptbr_to_float(val):
    if val is None: return None
    if isinstance(val, (int, float)): return float(val)
    s = re.sub(r"[^\d,.\-]", "", str(val).strip()).replace(".", "").replace(",", ".")
    try: return float(s)
    except (ValueError, TypeError): return None

def current_loja():
    return (session.get("loja_codigo") or "").strip()

def require_login(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        loja_codigo = current_loja()
        usuario_id = session.get("usuario_id")
        if not loja_codigo or not usuario_id:
            return jsonify(error="Não autenticado. Faça login primeiro."), 401
        loja = get_loja_by_codigo(loja_codigo)
        if not loja or loja.get("status") != "aprovado":
            session.clear()
            return jsonify(error="Sessão inválida. Faça login novamente."), 401
        usuario = get_usuario_by_id(usuario_id)
        if not usuario or usuario.get("loja") != loja_codigo or not usuario.get("ativo"):
            session.clear()
            return jsonify(error="Sessão inválida. Faça login novamente."), 401
        return func(*args, **kwargs)
    return wrapper

def require_role(*papeis):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if session.get("papel") not in papeis:
                return jsonify(error="Acesso restrito: permissão insuficiente."), 403
            return func(*args, **kwargs)
        return wrapper
    return decorator

def require_admin(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization") or ""
        # Lido a cada request (não no import) para não congelar o valor
        # quando o ambiente muda depois que o módulo já foi importado.
        token_esperado = os.environ.get("ADMIN_TOKEN") or ""
        if not token_esperado or not hmac.compare_digest(token, token_esperado):
            return jsonify(error="Acesso negado."), 403
        return func(*args, **kwargs)
    return wrapper

def _carregar_pedido_itens(pedido_id: int, loja_codigo: str):
    pedido = get_pedido(pedido_id) if callable(get_pedido) else None
    if not pedido or pedido.get('loja_codigo') != loja_codigo:
        return None, None
    itens = get_itens_pedido(pedido_id) if callable(get_itens_pedido) else []
    return pedido, itens

# -----------------------------------------------------------------------------
# Rotas de Autenticação e Servir Arquivos
# -----------------------------------------------------------------------------
@app.post("/auth/signup")
def auth_signup():
    data = request.get_json(silent=True) or {}
    nome_loja = (data.get("nome_loja") or "").strip()
    nome = (data.get("nome") or "").strip()
    login = (data.get("login") or "").strip()
    senha = (data.get("senha") or "").strip()
    if not nome_loja or not nome or not login or not senha:
        return jsonify(error="Nome da loja, seu nome, login e senha são obrigatórios."), 400
    senha_hash = generate_password_hash(senha)
    conn = get_conn()
    try:
        # Loja (tenant, pendente de aprovação) e primeiro usuário admin
        # nascem juntos, na mesma transação.
        conn.execute("BEGIN")
        conn.execute("INSERT INTO lojas (codigo, nome) VALUES (?, ?)", (nome_loja, nome_loja))
        conn.execute(
            "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao, ativo) VALUES (?, ?, ?, ?, 'admin', 0, 1)",
            (nome_loja, nome, login, senha_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "usuarios" in str(e):
            return jsonify(error="Já existe um usuário com este login nesta loja."), 409
        return jsonify(error="Já existe uma loja com este nome."), 409
    finally:
        conn.close()
    return jsonify(ok=True, message="Loja criada com sucesso."), 201

@app.post("/auth/login")
@limiter.limit("5 per minute; 30 per hour")
def auth_login():
    data = request.get_json(silent=True) or {}
    nome_loja = (data.get("nome_loja") or "").strip()
    login = (data.get("login") or "").strip()
    senha = (data.get("senha") or "").strip()
    if not nome_loja or not login or not senha:
        return jsonify(error="Nome da loja, login e senha são obrigatórios."), 400
    loja_db = get_loja_by_codigo(nome_loja)
    usuario = get_usuario(nome_loja, login)
    if (
        loja_db and loja_db.get("status") == "aprovado"
        and usuario and usuario.get("ativo")
        and check_password_hash(usuario["senha_hash"], senha)
    ):
        session["loja_codigo"] = loja_db["codigo"]
        session["usuario_id"] = usuario["id"]
        session["papel"] = usuario["papel"]
        return jsonify(ok=True, loja=loja_db["codigo"], usuario=usuario["nome"], papel=usuario["papel"])
    # Mensagem única de propósito: não revelar se falhou loja, usuário ou senha.
    return jsonify(error="Credenciais inválidas."), 401

@app.post("/auth/logout")
def auth_logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/")
def index():
    if current_loja():
        return send_from_directory(HTML_DIR, "index.html")
    return redirect(url_for("acesso_page"))

@app.get("/acesso", endpoint="acesso_page")
def acesso_page():
    return send_from_directory(HTML_DIR, "acesso.html")

@app.get("/admin", endpoint="admin_page")
def admin_page():
    return send_from_directory(HTML_DIR, "admin.html")

@app.get("/<path:folder>/<path:filename>")
def serve_static_folder(folder, filename):
    if folder in ["css", "js", "html"]:
        return send_from_directory(FRONT_DIR / folder, filename)
    abort(404)

# -----------------------------------------------------------------------------
# API: Clientes, Vendedores
# -----------------------------------------------------------------------------
@app.route("/clientes", methods=["GET", "POST"])
@require_login
def clientes_api():
    loja = current_loja()
    conn = get_conn()
    if request.method == "GET":
        search_query = request.args.get("search", "").strip()
        sql = "SELECT id, nome, telefone, email FROM clientes WHERE loja = ? AND nome LIKE ? ORDER BY nome ASC"
        clientes = [dict(r) for r in conn.execute(sql, (loja, f"%{search_query}%")).fetchall()]
        conn.close()
        return jsonify(clientes)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        nome = (data.get("nome") or "").strip()
        if not nome:
            conn.close(); return jsonify(error="Nome é obrigatório."), 400
        try:
            cur = conn.execute("INSERT INTO clientes (nome, telefone, email, loja) VALUES (?, ?, ?, ?)", (nome, data.get("telefone"), data.get("email"), loja))
            new_id = cur.lastrowid
            conn.commit()
            return jsonify(id=new_id, **data), 201
        except sqlite3.Error as e:
            conn.rollback(); return jsonify(error=f"Erro no banco de dados: {e}"), 500
        finally: conn.close()

@app.route("/clientes/<int:cliente_id>", methods=["DELETE"])
@require_login
def cliente_delete_api(cliente_id: int):
    loja = current_loja()
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM clientes WHERE id = ? AND loja = ?", (cliente_id, loja))
        if cur.rowcount == 0:
            conn.close(); return jsonify(error="Cliente não encontrado."), 404
        conn.commit()
        return jsonify(ok=True, message="Cliente removido com sucesso.")
    except sqlite3.Error as e:
        conn.rollback(); return jsonify(error=f"Erro de banco de dados: {e}"), 500
    finally: conn.close()

@app.put("/clientes/<int:cliente_id>")
@require_login
def cliente_update_api(cliente_id: int):
    loja = current_loja()
    data = request.get_json(silent=True) or {}
    telefone = data.get("telefone", "")
    email = data.get("email", "")

    conn = get_conn()
    try:
        cur = conn.execute("UPDATE clientes SET telefone = ?, email = ? WHERE id = ? AND loja = ?",
                           (telefone, email, cliente_id, loja))
        if cur.rowcount == 0:
            conn.close(); return jsonify(error="Cliente não encontrado."), 404
        
        conn.commit()
        return jsonify(ok=True, message="Cliente atualizado com sucesso.")
    except sqlite3.Error as e:
        conn.rollback(); return jsonify(error=f"Erro de banco de dados: {e}"), 500
    finally:
        conn.close()

@app.get("/me")
@require_login
def me_api():
    usuario = get_usuario_by_id(session.get("usuario_id"))
    return jsonify(
        loja=current_loja(),
        usuario_id=usuario["id"],
        nome=usuario["nome"],
        papel=usuario["papel"],
    )

# -----------------------------------------------------------------------------
# API: Usuários (gestão de operadores — somente admin da loja)
# -----------------------------------------------------------------------------
@app.route("/usuarios", methods=["GET", "POST"])
@require_login
@require_role("admin")
def usuarios_api():
    loja = current_loja()
    conn = get_conn()
    if request.method == "GET":
        usuarios = [dict(r) for r in conn.execute(
            "SELECT id, nome, login, papel, taxa_comissao, ativo FROM usuarios WHERE loja = ? ORDER BY nome ASC",
            (loja,)
        ).fetchall()]
        conn.close(); return jsonify(usuarios)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        nome = (data.get("nome") or "").strip()
        login = (data.get("login") or "").strip()
        senha = (data.get("senha") or "").strip()
        taxa = _ptbr_to_float(data.get("taxa_comissao"))
        if taxa is None: taxa = 0.0
        if not nome or not login or not senha:
            conn.close(); return jsonify(error="Nome, login e senha são obrigatórios."), 400
        if taxa < 0:
            conn.close(); return jsonify(error="Taxa de comissão não pode ser negativa."), 400
        try:
            cur = conn.execute(
                "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao, ativo) VALUES (?, ?, ?, ?, 'operador', ?, 1)",
                (loja, nome, login, generate_password_hash(senha), taxa),
            )
            new_id = cur.lastrowid
            conn.commit()
            return jsonify(id=new_id, nome=nome, login=login, papel="operador", taxa_comissao=taxa), 201
        except sqlite3.IntegrityError:
            conn.rollback(); return jsonify(error=f"Já existe um usuário com o login '{login}' nesta loja."), 409
        finally: conn.close()

@app.put("/usuarios/<int:usuario_id>")
@require_login
@require_role("admin")
def usuario_update_api(usuario_id: int):
    loja = current_loja()
    data = request.get_json(silent=True) or {}
    conn = get_conn()
    try:
        alvo = conn.execute(
            "SELECT id, nome, taxa_comissao, ativo FROM usuarios WHERE id = ? AND loja = ?",
            (usuario_id, loja)
        ).fetchone()
        if not alvo:
            return jsonify(error="Usuário não encontrado."), 404

        nome = (data.get("nome") or "").strip() or alvo["nome"]
        taxa = _ptbr_to_float(data.get("taxa_comissao"))
        if taxa is None: taxa = alvo["taxa_comissao"]
        if taxa < 0:
            return jsonify(error="Taxa de comissão não pode ser negativa."), 400
        ativo = 1 if data.get("ativo", alvo["ativo"]) in (1, True, "1", "true") else 0
        if usuario_id == session.get("usuario_id") and not ativo:
            return jsonify(error="Você não pode desativar a si mesmo."), 400

        conn.execute(
            "UPDATE usuarios SET nome = ?, taxa_comissao = ?, ativo = ? WHERE id = ? AND loja = ?",
            (nome, taxa, ativo, usuario_id, loja),
        )
        senha_nova = (data.get("senha") or "").strip()
        if senha_nova:
            conn.execute(
                "UPDATE usuarios SET senha_hash = ? WHERE id = ? AND loja = ?",
                (generate_password_hash(senha_nova), usuario_id, loja),
            )
        conn.commit()
        return jsonify(ok=True, message="Usuário atualizado.")
    except sqlite3.Error as e:
        conn.rollback(); return jsonify(error=f"Erro de banco de dados: {e}"), 500
    finally:
        conn.close()

# -----------------------------------------------------------------------------
# API: Pedidos com Lógica de Estoque
# -----------------------------------------------------------------------------
@app.route("/pedidos", methods=["GET", "POST"])
@require_login
def pedidos_api():
    loja = current_loja()
    conn = get_conn()
    if request.method == "GET":
        sort_by = request.args.get("sort", "data_desc")
        order_options = {"data_desc": "p.id DESC", "data_asc": "p.id ASC", "cliente_asc": "cliente_nome ASC, p.id DESC", "total_desc": "p.total DESC", "total_asc": "p.total ASC"}
        order_clause = order_options.get(sort_by, "p.id DESC")
        sql = f"SELECT p.id, p.data_iso, p.tecido, p.total, p.desconto, p.comissao_valor, c.nome AS cliente_nome, COALESCE(u.nome, '') AS vendedor_nome FROM pedidos p JOIN clientes c ON c.id = p.cliente_id LEFT JOIN usuarios u ON u.id = p.usuario_id WHERE p.loja = ? ORDER BY {order_clause} LIMIT 200"
        pedidos_raw = conn.execute(sql, (loja,)).fetchall()
        pedidos = [dict(p) for p in pedidos_raw]
        conn.close()
        return jsonify(pedidos)
    
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cliente_id = data.get("cliente_id")
        preco_unitario = _ptbr_to_float(data.get("preco_unitario"))
        itens_in = data.get("itens") or []
        descontar_estoque = data.get("descontar_estoque", False)
        tecido_nome = (data.get("tecido") or "").strip()

        if not all([cliente_id, preco_unitario is not None, itens_in, tecido_nome]):
            conn.close()
            return jsonify(error="Dados incompletos."), 400

        if not conn.execute("SELECT 1 FROM clientes WHERE id = ? AND loja = ?", (cliente_id, loja)).fetchone():
            conn.close()
            return jsonify(error="Cliente inválido para esta loja."), 400

        total_kg = sum(_ptbr_to_float(it.get("peso", 0)) for it in itens_in)
        desconto = _ptbr_to_float(data.get("desconto")) or 0.0
        total = round((total_kg * preco_unitario) - desconto, 2)

        # O vendedor é sempre o usuário logado; a comissão é congelada aqui
        # com a taxa vigente — mudanças futuras não afetam este pedido.
        usuario_id = session.get("usuario_id")
        vendedor = get_usuario_by_id(usuario_id)
        comissao_taxa = float(vendedor.get("taxa_comissao") or 0)
        comissao_valor = round(total * comissao_taxa / 100, 2)
        
        try:
            conn.execute("BEGIN")
            
            if descontar_estoque:
                tecido_row = conn.execute("SELECT id FROM estoque_tecidos WHERE nome_tecido = ? AND loja = ?", (tecido_nome, loja)).fetchone()
                if not tecido_row:
                    raise sqlite3.IntegrityError(f"Tipo de tecido '{tecido_nome}' não encontrado no estoque.")
                tecido_id = tecido_row['id']

                for item in itens_in:
                    cor_nome = (item.get("cor") or "").strip()
                    peso_pedido = _ptbr_to_float(item.get("peso", 0))
                    if not cor_nome or peso_pedido <= 0: continue
                    cor_row = conn.execute("SELECT id, peso_kg, qtd_pecas FROM estoque_cores WHERE tecido_id = ? AND nome_cor = ?", (tecido_id, cor_nome)).fetchone()
                    if not cor_row or cor_row['peso_kg'] < peso_pedido or cor_row['qtd_pecas'] < 1:
                        if not cor_row:
                            msg_erro = f"Cor '{cor_nome}' não encontrada no estoque de '{tecido_nome}'."
                        elif cor_row['peso_kg'] < peso_pedido:
                            msg_erro = f"Peso insuficiente para {tecido_nome} - cor {cor_nome}. (Disponível: {cor_row['peso_kg']} kg)"
                        else:
                            msg_erro = f"Não há peças disponíveis para {tecido_nome} - cor {cor_nome}."
                        raise sqlite3.IntegrityError(msg_erro)

                    conn.execute("UPDATE estoque_cores SET peso_kg = peso_kg - ?, qtd_pecas = qtd_pecas - 1 WHERE id = ?", (peso_pedido, cor_row['id']))

            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
            cur_pedido = conn.execute(
                "INSERT INTO pedidos (cliente_id, tecido, quantidade, preco_unitario, total, desconto, loja, data_iso, usuario_id, comissao_taxa, comissao_valor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cliente_id, tecido_nome, len(itens_in), preco_unitario, total, desconto, loja, now_iso, usuario_id, comissao_taxa, comissao_valor)
            )
            pedido_id = cur_pedido.lastrowid

            itens_to_insert = [(pedido_id, tecido_nome, it.get("cor"), _ptbr_to_float(it.get("peso"))) for it in itens_in]
            conn.executemany("INSERT INTO itens_pedido (pedido_id, descricao, cor, peso_kg) VALUES (?, ?, ?, ?)", itens_to_insert)
            
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            return jsonify(error=f"Erro de banco de dados: {e}"), 500
        finally:
            conn.close()
            
        return jsonify(id=pedido_id, total=total), 201

@app.route("/pedidos/<int:pedido_id>", methods=["GET", "PUT", "DELETE"])
@require_login
def pedido_single_api(pedido_id: int):
    loja = current_loja()
    conn = get_conn()
    if request.method == "GET":
        pedido, itens = _carregar_pedido_itens(pedido_id, loja)
        if not pedido:
            conn.close(); return jsonify(error="Pedido não encontrado."), 404
        conn.close(); return jsonify(pedido=dict(pedido), itens=itens)
        
    if request.method == "DELETE":
        try:
            cur = conn.execute("DELETE FROM pedidos WHERE id = ? AND loja = ?", (pedido_id, loja))
            if cur.rowcount == 0:
                conn.close(); return jsonify(error="Pedido não encontrado."), 404
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback(); return jsonify(error=f"Erro no banco de dados: {e}"), 500
        finally: conn.close()
        return jsonify(ok=True)
    
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        # NOTA: A lógica aqui NÃO afeta o estoque. É apenas para correção de dados do romaneio.
        try:
            cliente_id = data.get("cliente_id")
            tecido_nome = (data.get("tecido") or "").strip()
            preco_unitario = _ptbr_to_float(data.get("preco_unitario"))
            desconto = _ptbr_to_float(data.get("desconto")) or 0.0
            itens_in = data.get("itens") or []

            total_kg = sum(_ptbr_to_float(it.get("peso", 0)) for it in itens_in)
            total = round((total_kg * preco_unitario) - desconto, 2)

            if not conn.execute("SELECT 1 FROM clientes WHERE id = ? AND loja = ?", (cliente_id, loja)).fetchone():
                return jsonify(error="Cliente inválido para esta loja."), 400

            conn.execute("BEGIN")
            # O vendedor (usuario_id) não muda na edição; a comissão é
            # recalculada sobre o novo total usando a taxa JÁ CONGELADA
            # no pedido, não a taxa atual do usuário.
            cur = conn.execute("""
                UPDATE pedidos SET cliente_id=?, tecido=?, preco_unitario=?, desconto=?, total=?,
                    comissao_valor = ROUND(? * comissao_taxa / 100, 2)
                WHERE id=? AND loja=?
            """, (cliente_id, tecido_nome, preco_unitario, desconto, total, total, pedido_id, loja))
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify(error="Pedido não encontrado."), 404

            conn.execute("DELETE FROM itens_pedido WHERE pedido_id=?", (pedido_id,))
            
            itens_to_insert = [(pedido_id, tecido_nome, it.get("cor"), _ptbr_to_float(it.get("peso"))) for it in itens_in]
            conn.executemany("INSERT INTO itens_pedido (pedido_id, descricao, cor, peso_kg) VALUES (?, ?, ?, ?)", itens_to_insert)
            
            conn.commit()
            return jsonify(ok=True, message="Pedido atualizado.")
        except sqlite3.Error as e:
            conn.rollback()
            return jsonify(error=f"Erro no banco de dados: {e}"), 500
        finally:
            conn.close()

# -----------------------------------------------------------------------------
# API: Estoque
# -----------------------------------------------------------------------------
@app.get("/api/estoque")
@require_login
def get_estoque():
    loja = current_loja()
    conn = get_conn()
    tecidos_raw = conn.execute("SELECT id, nome_tecido FROM estoque_tecidos WHERE loja = ? ORDER BY nome_tecido", (loja,)).fetchall()
    
    estoque = []
    for tecido in tecidos_raw:
        cores = conn.execute(
            "SELECT id, nome_cor, peso_kg, qtd_pecas FROM estoque_cores WHERE tecido_id = ? ORDER BY nome_cor",
            (tecido['id'],)
        ).fetchall()
        estoque.append({
            "id": tecido['id'],
            "nome_tecido": tecido['nome_tecido'],
            "cores": [dict(c) for c in cores]
        })
    conn.close()
    return jsonify(estoque)

@app.post("/api/estoque/tecidos")
@require_login
def add_tecido_estoque():
    loja = current_loja()
    data = request.get_json()
    nome_tecido = (data.get("nome_tecido") or "").strip()
    if not nome_tecido:
        return jsonify(error="Nome do tecido é obrigatório."), 400
    
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES (?, ?)", (nome_tecido, loja))
        new_id = cur.lastrowid
        conn.commit()
        return jsonify(id=new_id, nome_tecido=nome_tecido), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify(error=f"O tecido '{nome_tecido}' já está cadastrado."), 409
    finally:
        conn.close()

@app.post("/api/estoque/cores")
@require_login
def add_cor_estoque():
    loja = current_loja()
    data = request.get_json()
    tecido_id = data.get("tecido_id")
    nome_cor = (data.get("nome_cor") or "").strip()
    peso_kg = _ptbr_to_float(data.get("peso_kg")) or 0
    qtd_pecas = data.get("qtd_pecas") or 0
    
    if not all([tecido_id, nome_cor]):
        return jsonify(error="ID do tecido e nome da cor são obrigatórios."), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM estoque_tecidos WHERE id = ? AND loja = ?", (tecido_id, loja))
        if not cur.fetchone():
            return jsonify(error="Tecido não encontrado nesta loja."), 404
        cur.execute("SELECT id, peso_kg, qtd_pecas FROM estoque_cores WHERE tecido_id = ? AND nome_cor = ?", (tecido_id, nome_cor))
        existing = cur.fetchone()
        
        if existing:
            novo_peso = existing['peso_kg'] + peso_kg
            novas_pecas = existing['qtd_pecas'] + qtd_pecas
            cur.execute("UPDATE estoque_cores SET peso_kg = ?, qtd_pecas = ? WHERE id = ?", (novo_peso, novas_pecas, existing['id']))
        else:
            cur.execute("INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (?, ?, ?, ?)",
                        (tecido_id, nome_cor, peso_kg, qtd_pecas))
        
        conn.commit()
        return jsonify(ok=True, message="Estoque atualizado."), 200
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify(error=f"Erro de banco de dados: {e}"), 500
    finally:
        conn.close()

@app.delete("/api/estoque/tecidos/<int:tecido_id>")
@require_login
def delete_tecido(tecido_id):
    loja = current_loja()
    conn = get_conn()
    cur = conn.execute("DELETE FROM estoque_tecidos WHERE id = ? AND loja = ?", (tecido_id, loja))
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Tecido não encontrado ou não pertence a esta loja."), 404
    conn.commit()
    conn.close()
    return jsonify(ok=True)

@app.delete("/api/estoque/cores/<int:cor_id>")
@require_login
def delete_cor(cor_id):
    loja = current_loja()
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM estoque_cores WHERE id = ? AND tecido_id IN (SELECT id FROM estoque_tecidos WHERE loja = ?)",
        (cor_id, loja)
    )
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="Cor não encontrada."), 404
    conn.commit()
    conn.close()
    return jsonify(ok=True)

@app.put("/api/estoque/cores/<int:cor_id>")
@require_login
def update_cor_estoque(cor_id):
    loja = current_loja()
    data = request.get_json()
    novo_peso_kg = _ptbr_to_float(data.get("peso_kg"))
    novas_qtd_pecas = data.get("qtd_pecas")

    if novo_peso_kg is None or novas_qtd_pecas is None or novo_peso_kg < 0 or novas_qtd_pecas < 0:
        return jsonify(error="Valores de peso e quantidade de peças são obrigatórios e não podem ser negativos."), 400

    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE estoque_cores SET peso_kg = ?, qtd_pecas = ? WHERE id = ? AND tecido_id IN (SELECT id FROM estoque_tecidos WHERE loja = ?)",
            (novo_peso_kg, novas_qtd_pecas, cor_id, loja)
        )
        
        if cur.rowcount == 0:
            conn.close(); return jsonify(error="Cor não encontrada no estoque."), 404
            
        conn.commit()
        return jsonify(ok=True, message="Estoque ajustado com sucesso.")
    except sqlite3.Error as e:
        conn.rollback(); return jsonify(error=f"Erro de banco de dados: {e}"), 500
    finally:
        conn.close()
# -----------------------------------------------------------------------------
# API: PIX e Exportações
# -----------------------------------------------------------------------------
@app.post("/pix")
@require_login
@require_role("admin")
def pix_salvar():
    loja = current_loja()
    data = request.get_json(silent=True) or {}
    pix_chave = (data.get("pix_chave") or "").strip()
    if callable(salvar_pix_by_codigo):
        salvar_pix_by_codigo(codigo=loja, pix_chave=pix_chave)
        return jsonify(ok=True, message="Chave PIX salva com sucesso.")
    return jsonify(error="Função de salvar PIX indisponível."), 500

@app.get("/exportar/<int:pedido_id>")
@require_login
def exportar_pedido(pedido_id: int):
    loja_codigo = current_loja()
    tipo = (request.args.get("type") or "png").lower()
    pedido, itens = _carregar_pedido_itens(pedido_id, loja_codigo)
    if not pedido:
        return jsonify(error="Pedido não encontrado ou não pertence à sua loja."), 404
    loja_info = get_loja_by_codigo(loja_codigo) or {}
    if tipo == "png":
        if not callable(render_pedido_png):
            return jsonify(error="Serviço de renderização de PNG indisponível."), 500
        buf = render_pedido_png(loja=loja_info, pedido=pedido, itens=itens)
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"romaneio_{pedido_id}.png")
    elif tipo == "pdf":
        if not callable(render_pedido_pdf):
            return jsonify(error="Serviço de renderização de PDF indisponível."), 500
        with app.app_context():
            buf = render_pedido_pdf(loja=loja_info, pedido=pedido, itens=itens)
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=f"romaneio_{pedido_id}.pdf")
    return jsonify(error="Tipo de exportação inválido."), 400

# -----------------------------------------------------------------------------
# Rotas de Admin
# -----------------------------------------------------------------------------
@app.get("/admin/lojas")
@require_admin
def admin_get_todas_lojas():
    conn = get_conn()
    lojas = [dict(r) for r in conn.execute("SELECT codigo, nome, status, created_at FROM lojas ORDER BY id DESC").fetchall()]
    conn.close(); return jsonify(lojas)

@app.post("/admin/aprovar-loja/<codigo_loja>")
@require_admin
def admin_aprovar_loja(codigo_loja):
    conn = get_conn()
    conn.execute("UPDATE lojas SET status = 'aprovado' WHERE codigo = ?", (codigo_loja,))
    conn.commit(); conn.close()
    return jsonify(ok=True, message=f"Loja {codigo_loja} aprovada.")

@app.post("/admin/revogar-loja/<codigo_loja>")
@require_admin
def admin_revogar_loja(codigo_loja):
    conn = get_conn()
    conn.execute("UPDATE lojas SET status = 'revogado' WHERE codigo = ?", (codigo_loja,))
    conn.commit(); conn.close()
    return jsonify(ok=True, message=f"Acesso da loja {codigo_loja} foi revogado.")

@app.post("/admin/reativar-loja/<codigo_loja>")
@require_admin
def admin_reativar_loja(codigo_loja):
    conn = get_conn()
    conn.execute("UPDATE lojas SET status = 'aprovado' WHERE codigo = ?", (codigo_loja,))
    conn.commit(); conn.close()
    return jsonify(ok=True, message=f"Acesso da loja {codigo_loja} foi reativado.")

@app.post("/admin/deletar-loja/<codigo_loja>")
@require_admin
def admin_deletar_loja(codigo_loja):
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM lojas WHERE codigo = ?", (codigo_loja,))
        if cur.rowcount == 0:
            conn.close(); return jsonify(error="Loja não encontrada."), 404
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback(); return jsonify(error=f"Erro no banco de dados: {e}"), 500
    finally: conn.close()
    return jsonify(ok=True, message=f"Loja {codigo_loja} deletada com sucesso.")

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)

