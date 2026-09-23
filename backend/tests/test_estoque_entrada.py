# Testes da baixa de estoque POR PESO no POST /pedidos e da Entrada de
# mercadoria (lote digitado + importação de planilha csv/xlsx).
import io
import os
import sys
import uuid
from pathlib import Path

import openpyxl
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

LOJA = "Loja Estoque"
SENHA = "s3nh4-forte"


def _login(login):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, gerente e operador; tecido Malha com Azul = 10 kg / 3 peças."""
    database.DB_PATH = str(_TMP_DB)
    _TMP_DB.unlink(missing_ok=True)
    database.init_db()

    c = app.test_client()
    assert c.post(
        "/auth/signup",
        json={"nome_loja": LOJA, "nome": "Dono", "login": "admin", "senha": SENHA},
    ).status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=?", (LOJA,))
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente X', ?)", (LOJA,)
    ).lastrowid
    for login, papel in (("gerente", "gerente"), ("operador", "operador")):
        conn.execute(
            "INSERT INTO usuarios (loja, nome, login, senha_hash, papel) VALUES (?, ?, ?, ?, ?)",
            (LOJA, login.title(), login, generate_password_hash(SENHA), papel),
        )
    tecido_id = conn.execute(
        "INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Malha', ?)", (LOJA,)
    ).lastrowid
    conn.execute(
        "INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (?, 'Azul', 10, 3)",
        (tecido_id,),
    )
    conn.commit()
    conn.close()

    yield {
        "admin": _login("admin"),
        "gerente": _login("gerente"),
        "op": _login("operador"),
        "cliente_id": cliente_id,
        "tecido_id": tecido_id,
    }
    _TMP_DB.unlink(missing_ok=True)


def _cor(nome, tecido="Malha"):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT c.peso_kg, c.qtd_pecas FROM estoque_cores c "
        "JOIN estoque_tecidos t ON t.id = c.tecido_id "
        "WHERE t.loja = ? AND t.nome_tecido = ? AND c.nome_cor = ?",
        (LOJA, tecido, nome),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _count(tabela):
    conn = database.get_conn()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {tabela}").fetchone()["n"]
    conn.close()
    return n


def _vender(c, cliente_id, itens):
    return c.post(
        "/pedidos",
        json={
            "cliente_id": cliente_id,
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": itens,
            "descontar_estoque": True,
        },
    )


# ---------------------------------------------------------------------------
# A) Baixa por peso
# ---------------------------------------------------------------------------
def test_baixa_debita_o_peso_vendido(ctx):
    resp = _vender(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "2,5"}])
    assert resp.status_code == 201, resp.get_json()
    assert _cor("Azul")["peso_kg"] == pytest.approx(7.5)


def test_baixa_soma_varias_linhas_da_mesma_cor(ctx):
    # Várias linhas da mesma cor saem do total da cor, sem resíduo de float.
    itens = [{"cor": "Azul", "peso": "0,1"}, {"cor": "Azul", "peso": "0,2"}, {"cor": "Azul", "peso": "4"}]
    assert _vender(ctx["op"], ctx["cliente_id"], itens).status_code == 201
    assert _cor("Azul")["peso_kg"] == 5.7


def test_baixa_nao_mexe_em_qtd_pecas(ctx):
    # 4 linhas com só 3 peças no estoque: o que manda é o peso.
    itens = [{"cor": "Azul", "peso": "1"} for _ in range(4)]
    assert _vender(ctx["op"], ctx["cliente_id"], itens).status_code == 201
    cor = _cor("Azul")
    assert cor["peso_kg"] == pytest.approx(6.0)
    assert cor["qtd_pecas"] == 3


def test_baixa_permite_zerar_a_cor(ctx):
    assert _vender(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "10"}]).status_code == 201
    assert _cor("Azul")["peso_kg"] == 0


def test_baixa_bloqueia_venda_sem_kg_suficiente(ctx):
    resp = _vender(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "10,5"}])
    assert resp.status_code == 409
    assert "insuficiente" in resp.get_json()["error"].lower()
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("pedidos") == 0


def test_baixa_bloqueia_quando_soma_das_linhas_excede(ctx):
    # Cada linha cabe sozinha, mas a soma (12 kg) não: nada é gravado.
    itens = [{"cor": "Azul", "peso": "6"}, {"cor": "Azul", "peso": "6"}]
    assert _vender(ctx["op"], ctx["cliente_id"], itens).status_code == 409
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("pedidos") == 0


def test_baixa_rollback_total_se_uma_cor_falha(ctx):
    itens = [{"cor": "Azul", "peso": "2"}, {"cor": "Inexistente", "peso": "1"}]
    resp = _vender(ctx["op"], ctx["cliente_id"], itens)
    assert resp.status_code == 409
    assert "não encontrada" in resp.get_json()["error"]
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("pedidos") == 0


def test_venda_sem_descontar_estoque_nao_mexe_no_peso(ctx):
    resp = ctx["op"].post(
        "/pedidos",
        json={"cliente_id": ctx["cliente_id"], "preco_unitario": "10", "tecido": "Malha",
              "itens": [{"cor": "Azul", "peso": "50"}]},
    )
    assert resp.status_code == 201
    assert _cor("Azul")["peso_kg"] == 10


# ---------------------------------------------------------------------------
# B) Entrada de mercadoria — lote
# ---------------------------------------------------------------------------
def test_entrada_em_lote_credita_e_cria_cor_nova(ctx):
    resp = ctx["gerente"].post(
        "/api/estoque/entradas",
        json={
            "fornecedor": "Tecelagem Sul",
            "data": "2026-09-20",
            "linhas": [
                {"tecido": "Malha", "cor": "Azul", "peso": "12,5"},
                {"tecido": "malha", "cor": "azul", "peso": 2.5},   # casa sem diferenciar caixa
                {"tecido": "Malha", "cor": "Vermelho", "peso": "8", "id_rolo": "R-001"},
            ],
        },
    )
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["linhas"] == 3
    assert body["peso_total"] == 23.0
    assert body["rolos"] == 1
    assert body["cores_criadas"] == [{"tecido": "Malha", "cor": "Vermelho"}]

    assert _cor("Azul")["peso_kg"] == 25.0
    assert _cor("Vermelho")["peso_kg"] == 8.0
    assert _cor("azul") is None  # não duplicou a cor

    conn = database.get_conn()
    entrada = conn.execute("SELECT loja, fornecedor, data FROM entradas").fetchone()
    rolos = [dict(r) for r in conn.execute("SELECT entrada_id, tecido, cor, roll_ext_id, peso FROM rolos").fetchall()]
    conn.close()
    assert dict(entrada) == {"loja": LOJA, "fornecedor": "Tecelagem Sul", "data": "2026-09-20"}
    assert rolos == [{"entrada_id": body["id"], "tecido": "Malha", "cor": "Vermelho", "roll_ext_id": "R-001", "peso": 8.0}]


def test_entrada_e_depois_venda_debita_do_credito(ctx):
    ctx["admin"].post("/api/estoque/entradas", json={"linhas": [{"tecido": "Malha", "cor": "Verde", "peso": "5"}]})
    assert _vender(ctx["op"], ctx["cliente_id"], [{"cor": "Verde", "peso": "3,2"}]).status_code == 201
    assert _cor("Verde")["peso_kg"] == 1.8


def test_entrada_com_linha_invalida_nao_grava_nada(ctx):
    resp = ctx["admin"].post(
        "/api/estoque/entradas",
        json={"linhas": [
            {"tecido": "Malha", "cor": "Azul", "peso": "5"},
            {"tecido": "Malha", "cor": "", "peso": "5"},
            {"tecido": "Malha", "cor": "Rosa", "peso": "0"},
        ]},
    )
    assert resp.status_code == 400
    assert [e["linha"] for e in resp.get_json()["erros"]] == [2, 3]
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("entradas") == 0


def test_entrada_com_tecido_nao_cadastrado_e_recusada(ctx):
    resp = ctx["admin"].post(
        "/api/estoque/entradas",
        json={"linhas": [{"tecido": "Malha", "cor": "Azul", "peso": "5"},
                         {"tecido": "Brim", "cor": "Preto", "peso": "5"}]},
    )
    assert resp.status_code == 400
    assert "Brim" in resp.get_json()["error"]
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("entradas") == 0


def test_entrada_recusa_rolo_ja_recebido(ctx):
    linha = {"tecido": "Malha", "cor": "Azul", "peso": "5", "id_rolo": "R-9"}
    assert ctx["admin"].post("/api/estoque/entradas", json={"linhas": [linha]}).status_code == 201
    assert ctx["admin"].post("/api/estoque/entradas", json={"linhas": [linha]}).status_code == 409
    # Repetido dentro do mesmo lote também não passa.
    dup = [dict(linha, id_rolo="R-10"), dict(linha, id_rolo="R-10")]
    assert ctx["admin"].post("/api/estoque/entradas", json={"linhas": dup}).status_code == 400
    assert _cor("Azul")["peso_kg"] == 15


def test_entrada_sem_linhas_e_recusada(ctx):
    assert ctx["admin"].post("/api/estoque/entradas", json={"linhas": []}).status_code == 400


def test_entrada_isolada_por_loja(ctx):
    # Um tecido com o mesmo nome em outra loja não pode receber o crédito.
    conn = database.get_conn()
    conn.execute("INSERT INTO lojas (codigo, nome, status) VALUES ('Outra', 'Outra', 'aprovado')")
    outro = conn.execute("INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Linho', 'Outra')").lastrowid
    conn.execute("INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg) VALUES (?, 'Cru', 1)", (outro,))
    conn.commit()
    conn.close()
    resp = ctx["admin"].post("/api/estoque/entradas", json={"linhas": [{"tecido": "Linho", "cor": "Cru", "peso": "5"}]})
    assert resp.status_code == 400
    conn = database.get_conn()
    peso = conn.execute("SELECT peso_kg FROM estoque_cores WHERE tecido_id = ?", (outro,)).fetchone()["peso_kg"]
    conn.close()
    assert peso == 1


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------
def test_operador_recebe_403_ao_registrar_entrada(ctx):
    op = ctx["op"]
    assert op.post(
        "/api/estoque/entradas", json={"linhas": [{"tecido": "Malha", "cor": "Azul", "peso": "5"}]}
    ).status_code == 403
    assert op.post(
        "/api/estoque/entradas/importar",
        data={"arquivo": (io.BytesIO(b"tecido,cor,peso\nMalha,Azul,5\n"), "e.csv")},
        content_type="multipart/form-data",
    ).status_code == 403
    assert _cor("Azul")["peso_kg"] == 10


@pytest.mark.parametrize("quem", ["gerente", "admin"])
def test_gerente_e_admin_registram_entrada(ctx, quem):
    resp = ctx[quem].post(
        "/api/estoque/entradas", json={"linhas": [{"tecido": "Malha", "cor": "Azul", "peso": "1"}]}
    )
    assert resp.status_code == 201
    assert _cor("Azul")["peso_kg"] == 11


def test_entrada_exige_login():
    assert app.test_client().post("/api/estoque/entradas", json={"linhas": []}).status_code == 401


# ---------------------------------------------------------------------------
# Importação de planilha (só lê; quem grava é a confirmação)
# ---------------------------------------------------------------------------
def _importar(c, conteudo, nome):
    return c.post(
        "/api/estoque/entradas/importar",
        data={"arquivo": (io.BytesIO(conteudo), nome)},
        content_type="multipart/form-data",
    )


def test_importa_csv_com_ponto_e_virgula_e_acentos(ctx):
    csv_txt = "Tecido;Cor;Peso (kg);ID do Rolo\nMalha;Azul;12,5;R-1\nMalha;Lilás;3.25;\n\n"
    resp = _importar(ctx["admin"], csv_txt.encode("utf-8-sig"), "entrada.csv")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["erros"] == []
    assert body["linhas"] == [
        {"tecido": "Malha", "cor": "Azul", "peso": 12.5, "id_rolo": "R-1"},
        {"tecido": "Malha", "cor": "Lilás", "peso": 3.25, "id_rolo": None},
    ]
    # Importar não grava nada.
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("entradas") == 0


def test_importa_csv_latin1_com_virgula(ctx):
    csv_txt = "tecido,cor,peso\nMalha,Lilás,4\n"
    resp = _importar(ctx["gerente"], csv_txt.encode("latin-1"), "e.csv")
    assert resp.status_code == 200
    assert resp.get_json()["linhas"] == [{"tecido": "Malha", "cor": "Lilás", "peso": 4.0, "id_rolo": None}]


def test_importa_xlsx(ctx):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["tecido", "cor", "peso", "id_rolo"])
    ws.append(["Malha", "Azul", 7.5, 1001])
    ws.append(["Malha", "Preto", "2,75", None])
    ws.append([None, None, None, None])
    ws.append(["Malha", "", 1, None])
    buf = io.BytesIO()
    wb.save(buf)

    resp = _importar(ctx["admin"], buf.getvalue(), "entrada.xlsx")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["linhas"] == [
        {"tecido": "Malha", "cor": "Azul", "peso": 7.5, "id_rolo": "1001"},
        {"tecido": "Malha", "cor": "Preto", "peso": 2.75, "id_rolo": None},
    ]
    # Linha vazia é ignorada; a numeração do erro bate com a do Excel.
    assert body["erros"] == [{"linha": 4, "erro": "Campo obrigatório vazio: cor."}]


def test_importa_e_confirma_credita_estoque(ctx):
    resp = _importar(ctx["admin"], b"tecido,cor,peso\nMalha,Azul,5\nMalha,Branco,2\n", "e.csv")
    linhas = resp.get_json()["linhas"]
    resp = ctx["admin"].post("/api/estoque/entradas", json={"fornecedor": "X", "linhas": linhas})
    assert resp.status_code == 201
    assert _cor("Azul")["peso_kg"] == 15
    assert _cor("Branco")["peso_kg"] == 2


def test_importacao_rejeita_colunas_ausentes_e_formato(ctx):
    resp = _importar(ctx["admin"], b"tecido,cor\nMalha,Azul\n", "e.csv")
    assert resp.status_code == 400
    assert "peso" in resp.get_json()["error"]
    assert _importar(ctx["admin"], b"qualquer", "e.txt").status_code == 400
    assert _importar(ctx["admin"], b"nao e um zip", "e.xlsx").status_code == 400
    assert ctx["admin"].post("/api/estoque/entradas/importar").status_code == 400
