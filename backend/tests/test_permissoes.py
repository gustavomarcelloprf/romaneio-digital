# Testes dos papéis e permissões (operador / gerente / admin) e do
# reenquadramento da comissão como despesa do dono ("a pagar").
import os
import sys
import uuid
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

# app.py exige SECRET_KEY e ADMIN_TOKEN no import (boot fail-fast).
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

# Redireciona o banco para um arquivo temporário ANTES de importar o app.
_TMP_DB = Path(__file__).parent / f"_test_{uuid.uuid4().hex}.db"
database.DB_PATH = str(_TMP_DB)

import app as app_module  # noqa: E402
from app import app  # noqa: E402

# Rate limit do /auth/login desligado só nos testes (a suíte loga muitas vezes).
app_module.limiter.enabled = False

LOJA = "Loja Papeis"
SENHA = "s3nh4-forte"


def _login(login):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, operador (taxa 10%) e gerente (taxa 0)."""
    database.DB_PATH = str(_TMP_DB)
    _TMP_DB.unlink(missing_ok=True)
    database.init_db()

    admin = app.test_client()
    resp = admin.post(
        "/auth/signup",
        json={"nome_loja": LOJA, "nome": "Dono da Loja", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=?", (LOJA,))
    cur = conn.execute("INSERT INTO clientes (nome, loja) VALUES ('Cliente X', ?)", (LOJA,))
    cliente_id = cur.lastrowid
    # Ainda não há UI para criar gerente: o papel nasce direto no banco.
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (?, 'Gerente Um', 'gerente', ?, 'gerente', 0)",
        (LOJA, generate_password_hash(SENHA)),
    )
    admin_id = conn.execute(
        "SELECT id FROM usuarios WHERE loja=? AND login='admin'", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    admin = _login("admin")
    resp = admin.post(
        "/usuarios",
        json={"nome": "Operador Um", "login": "operador", "taxa_comissao": "10"},
    )
    assert resp.status_code == 201
    aceite = app.test_client().post(resp.get_json()["convite_path"], json={"senha": SENHA})
    assert aceite.status_code == 200

    yield {
        "admin": admin,
        "gerente": _login("gerente"),
        "op": _login("operador"),
        "cliente_id": cliente_id,
        "admin_id": admin_id,
    }
    _TMP_DB.unlink(missing_ok=True)


def _post_pedido(c, cliente_id, peso="1,0", preco="10,00"):
    return c.post(
        "/pedidos",
        json={
            "cliente_id": cliente_id,
            "preco_unitario": preco,
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": peso}],
        },
    )


# ---------------------------------------------------------------------------
# Schema: o papel 'gerente' é aceito
# ---------------------------------------------------------------------------
def test_schema_aceita_papel_gerente(ctx):
    conn = database.get_conn()
    papel = conn.execute(
        "SELECT papel FROM usuarios WHERE loja=? AND login='gerente'", (LOJA,)
    ).fetchone()["papel"]
    conn.close()
    assert papel == "gerente"


def test_criacao_de_usuario_continua_gerando_operador(ctx):
    # Não há UI para criar gerente ainda: mesmo pedindo, sai operador.
    resp = ctx["admin"].post(
        "/usuarios",
        json={"nome": "Outro", "login": "outro", "papel": "gerente"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["papel"] == "operador"


# ---------------------------------------------------------------------------
# Estoque: visualizar é livre, mutar exige gerente+admin
# ---------------------------------------------------------------------------
def test_operador_ve_estoque(ctx):
    assert ctx["op"].get("/api/estoque").status_code == 200


def test_operador_nao_muta_estoque(ctx):
    op = ctx["op"]
    assert op.post("/api/estoque/tecidos", json={"nome_tecido": "Malha"}).status_code == 403
    assert op.post(
        "/api/estoque/cores",
        json={"tecido_id": 1, "nome_cor": "Azul", "peso_kg": 5, "qtd_pecas": 2},
    ).status_code == 403
    assert op.delete("/api/estoque/tecidos/1").status_code == 403
    assert op.delete("/api/estoque/cores/1").status_code == 403
    assert op.put(
        "/api/estoque/cores/1", json={"peso_kg": 1, "qtd_pecas": 1}
    ).status_code == 403


def test_admin_e_gerente_mutam_estoque(ctx):
    assert ctx["admin"].post(
        "/api/estoque/tecidos", json={"nome_tecido": "Malha"}
    ).status_code == 201
    assert ctx["gerente"].post(
        "/api/estoque/tecidos", json={"nome_tecido": "Brim"}
    ).status_code == 201


def test_operador_continua_vendendo(ctx):
    # A trava do estoque não pode atrapalhar a venda.
    assert _post_pedido(ctx["op"], ctx["cliente_id"]).status_code == 201


# ---------------------------------------------------------------------------
# Dashboard da loja: gerente entra, mas sem os números de comissão
# ---------------------------------------------------------------------------
def test_gerente_ve_relatorio_da_loja_sem_comissoes(ctx):
    _post_pedido(ctx["op"], ctx["cliente_id"])

    resp = ctx["gerente"].get("/api/relatorio/loja")
    assert resp.status_code == 200
    rel = resp.get_json()
    assert "comissoes_a_pagar" not in rel
    assert rel["faturamento_total"] == 10.0
    # A coluna de comissão também some do ranking por operador.
    assert rel["por_operador"]
    assert all("comissao" not in o for o in rel["por_operador"])


def test_admin_ve_comissoes_a_pagar(ctx):
    _post_pedido(ctx["op"], ctx["cliente_id"])  # 10,00 × 10% = 1,00

    rel = ctx["admin"].get("/api/relatorio/loja").get_json()
    assert rel["comissoes_a_pagar"] == 1.0
    assert all("comissao" in o for o in rel["por_operador"])


def test_operador_continua_sem_o_relatorio_da_loja(ctx):
    assert ctx["op"].get("/api/relatorio/loja").status_code == 403


# ---------------------------------------------------------------------------
# Usuários e configurações continuam admin-only
# ---------------------------------------------------------------------------
def test_gerente_nao_gere_usuarios_nem_configuracoes(ctx):
    gerente = ctx["gerente"]
    assert gerente.get("/usuarios").status_code == 403
    assert gerente.post(
        "/usuarios", json={"nome": "X", "login": "x", "senha": SENHA}
    ).status_code == 403
    assert gerente.put(
        f"/usuarios/{ctx['admin_id']}", json={"taxa_comissao": 5}
    ).status_code == 403
    assert gerente.post("/pix", json={"pix_chave": "chave@pix"}).status_code == 403


# ---------------------------------------------------------------------------
# O admin é o dono: não recebe comissão
# ---------------------------------------------------------------------------
def test_venda_do_admin_nao_gera_comissao(ctx):
    pedido_id = _post_pedido(ctx["admin"], ctx["cliente_id"]).get_json()["id"]
    conn = database.get_conn()
    pedido = conn.execute(
        "SELECT comissao_taxa, comissao_valor FROM pedidos WHERE id=?", (pedido_id,)
    ).fetchone()
    conn.close()
    assert pedido["comissao_taxa"] == 0
    assert pedido["comissao_valor"] == 0


def test_venda_do_admin_nao_gera_comissao_nem_com_taxa_no_banco(ctx):
    # Mesmo que uma taxa escape para o banco (legado/import), a venda do
    # dono não pode virar comissão.
    conn = database.get_conn()
    conn.execute("UPDATE usuarios SET taxa_comissao=15 WHERE id=?", (ctx["admin_id"],))
    conn.commit()
    conn.close()

    pedido_id = _post_pedido(ctx["admin"], ctx["cliente_id"]).get_json()["id"]
    conn = database.get_conn()
    pedido = conn.execute(
        "SELECT comissao_valor FROM pedidos WHERE id=?", (pedido_id,)
    ).fetchone()
    conn.close()
    assert pedido["comissao_valor"] == 0


def test_taxa_do_admin_nao_e_editavel(ctx):
    resp = ctx["admin"].put(f"/usuarios/{ctx['admin_id']}", json={"taxa_comissao": "5"})
    assert resp.status_code == 400

    conn = database.get_conn()
    taxa = conn.execute(
        "SELECT taxa_comissao FROM usuarios WHERE id=?", (ctx["admin_id"],)
    ).fetchone()["taxa_comissao"]
    conn.close()
    assert taxa == 0


# ---------------------------------------------------------------------------
# /me entrega as permissões que montam o menu
# ---------------------------------------------------------------------------
def test_me_entrega_permissoes_por_papel(ctx):
    op = ctx["op"].get("/me").get_json()["permissoes"]
    assert op["vender"] is True
    assert op["estoque_ver"] is True
    assert op["estoque_mutar"] is False
    assert op["relatorio_loja"] is False
    assert op["usuarios_gerir"] is False

    ger = ctx["gerente"].get("/me").get_json()["permissoes"]
    assert ger["estoque_mutar"] is True
    assert ger["relatorio_loja"] is True
    assert ger["relatorio_comissoes"] is False
    assert ger["usuarios_gerir"] is False

    adm = ctx["admin"].get("/me").get_json()["permissoes"]
    assert all(adm[chave] for chave in adm)
