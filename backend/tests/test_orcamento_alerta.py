# Testes do ORÇAMENTO que vira pedido (status em pedidos + POST
# /pedidos/<id>/converter) e do ALERTA de estoque mínimo por cor.
import os
import sqlite3
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

LOJA = "Loja Orcamento"
OUTRA = "Loja Vizinha"
SENHA = "s3nh4-forte"


def _login(login, loja=LOJA):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": loja, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, gerente e operador (taxa 10%); Malha/Azul = 10 kg.

    Uma segunda loja aprovada (com sua própria Malha/Azul) serve para os
    testes de isolamento.
    """
    database.DB_PATH = str(_TMP_DB)
    _TMP_DB.unlink(missing_ok=True)
    database.init_db()

    c = app.test_client()
    for loja in (LOJA, OUTRA):
        assert c.post(
            "/auth/signup",
            json={"nome_loja": loja, "nome": "Dono", "login": "admin", "senha": SENHA},
        ).status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado'")
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente X', ?)", (LOJA,)
    ).lastrowid
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel) VALUES (?, 'Gerente', 'gerente', ?, 'gerente')",
        (LOJA, generate_password_hash(SENHA)),
    )
    op_id = conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) VALUES (?, 'Operador', 'operador', ?, 'operador', 10)",
        (LOJA, generate_password_hash(SENHA)),
    ).lastrowid
    cor_ids = {}
    for loja in (LOJA, OUTRA):
        tecido_id = conn.execute(
            "INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Malha', ?)", (loja,)
        ).lastrowid
        cor_ids[loja] = conn.execute(
            "INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (?, 'Azul', 10, 3)",
            (tecido_id,),
        ).lastrowid
    conn.commit()
    conn.close()

    yield {
        "admin": _login("admin"),
        "gerente": _login("gerente"),
        "op": _login("operador"),
        "outra_admin": _login("admin", OUTRA),
        "anon": app.test_client(),
        "cliente_id": cliente_id,
        "op_id": op_id,
        "cor_id": cor_ids[LOJA],
        "cor_id_outra": cor_ids[OUTRA],
    }
    _TMP_DB.unlink(missing_ok=True)


def _peso_azul(loja=LOJA):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT c.peso_kg FROM estoque_cores c JOIN estoque_tecidos t ON t.id = c.tecido_id "
        "WHERE t.loja = ? AND t.nome_tecido = 'Malha' AND c.nome_cor = 'Azul'",
        (loja,),
    ).fetchone()
    conn.close()
    return row["peso_kg"]


def _pedido_db(pedido_id):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT status, comissao_taxa, comissao_valor, total, descontar_estoque FROM pedidos WHERE id = ?",
        (pedido_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _criar(c, cliente_id, itens, orcamento, descontar_estoque=True):
    """R$ 10/kg; descontar_estoque=True por padrão (também no orçamento), para
    provar que o orçamento não baixa nada na criação mesmo com a intenção
    marcada — só a conversão respeita essa intenção depois."""
    return c.post(
        "/pedidos",
        json={
            "cliente_id": cliente_id,
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": itens,
            "descontar_estoque": descontar_estoque,
            "is_orcamento": orcamento,
        },
    )


def _orcamento(ctx, itens=None, cliente=None, descontar_estoque=True):
    resp = _criar(
        ctx[cliente or "op"], ctx["cliente_id"], itens or [{"cor": "Azul", "peso": "4"}], True, descontar_estoque
    )
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["status"] == "orcamento"
    return resp.get_json()["id"]


def _tudo(c, rota):
    # Período bem largo: data_iso é "agora", sempre cai dentro.
    return c.get(f"{rota}?de=2000-01-01&ate=2999-12-31").get_json()


# ---------------------------------------------------------------------------
# A) Orçamento: não baixa estoque, não congela comissão, não entra em relatório
# ---------------------------------------------------------------------------
def test_orcamento_nao_debita_estoque_e_comissao_zero(ctx):
    pid = _orcamento(ctx)
    assert _peso_azul() == 10
    p = _pedido_db(pid)
    assert p["status"] == "orcamento"
    assert p["comissao_valor"] == 0
    assert p["total"] == 40


def test_orcamento_nao_entra_nos_relatorios(ctx):
    _orcamento(ctx)

    loja = _tudo(ctx["admin"], "/api/relatorio/loja")
    assert loja["num_pedidos"] == 0
    assert loja["faturamento_total"] == 0
    assert loja["comissoes_a_pagar"] == 0
    assert loja["por_operador"] == []
    assert loja["tecidos_mais_vendidos"] == []
    # Orçamento não é venda: o tecido segue encalhado.
    assert loja["tecidos_encalhados"] == ["Malha"]

    meu = _tudo(ctx["op"], "/api/relatorio/meu")
    assert meu["num_pedidos"] == 0
    assert meu["faturamento"] == 0
    assert meu["comissao"] == 0
    assert meu["ultimos_pedidos"] == []


def test_listagem_separa_orcamentos_de_pedidos(ctx):
    oid = _orcamento(ctx)
    pid = _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}], False).get_json()["id"]

    pedidos = ctx["op"].get("/pedidos").get_json()
    assert [p["id"] for p in pedidos] == [pid]
    orcamentos = ctx["op"].get("/pedidos?status=orcamento").get_json()
    assert [o["id"] for o in orcamentos] == [oid]
    assert orcamentos[0]["status"] == "orcamento"


def test_pedido_default_continua_debitando_e_congelando(ctx):
    resp = _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "4"}], False)
    assert resp.status_code == 201
    assert resp.get_json()["status"] == "pedido"
    assert _peso_azul() == 6
    p = _pedido_db(resp.get_json()["id"])
    assert p["status"] == "pedido"
    assert p["comissao_valor"] == 4.0  # 10% de R$ 40

    # Sem is_orcamento no payload também é pedido (compatibilidade).
    resp = ctx["op"].post("/pedidos", json={
        "cliente_id": ctx["cliente_id"], "preco_unitario": "10,00", "tecido": "Malha",
        "itens": [{"cor": "Azul", "peso": "1"}], "descontar_estoque": True,
    })
    assert resp.get_json()["status"] == "pedido"
    assert _peso_azul() == 5


# ---------------------------------------------------------------------------
# B) Conversão orçamento -> pedido
# ---------------------------------------------------------------------------
def test_converter_debita_congela_taxa_atual_e_passa_a_contar(ctx):
    pid = _orcamento(ctx)
    # A taxa muda entre o orçamento e a conversão: vale a ATUAL.
    assert ctx["admin"].put(f"/usuarios/{ctx['op_id']}", json={"taxa_comissao": "5"}).status_code == 200

    resp = ctx["op"].post(f"/pedidos/{pid}/converter")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "pedido"

    assert _peso_azul() == 6
    p = _pedido_db(pid)
    assert p["status"] == "pedido"
    assert p["comissao_taxa"] == 5
    assert p["comissao_valor"] == 2.0  # 5% de R$ 40

    loja = _tudo(ctx["admin"], "/api/relatorio/loja")
    assert loja["num_pedidos"] == 1
    assert loja["faturamento_total"] == 40
    assert loja["comissoes_a_pagar"] == 2.0
    assert loja["por_operador"][0]["nome"] == "Operador"
    assert loja["tecidos_encalhados"] == []

    meu = _tudo(ctx["op"], "/api/relatorio/meu")
    assert meu["num_pedidos"] == 1
    assert meu["comissao"] == 2.0

    assert ctx["op"].get("/pedidos?status=orcamento").get_json() == []
    assert [p["id"] for p in ctx["op"].get("/pedidos").get_json()] == [pid]


def test_converter_comissao_e_do_vendedor_original_nao_de_quem_converte(ctx):
    # Orçamento do operador (10%) convertido pelo admin (dono, taxa 0).
    pid = _orcamento(ctx)
    assert ctx["admin"].post(f"/pedidos/{pid}/converter").status_code == 200
    assert _pedido_db(pid)["comissao_valor"] == 4.0


def test_converter_sem_estoque_da_409_e_nao_muda_nada(ctx):
    pid = _orcamento(ctx, itens=[{"cor": "Azul", "peso": "12"}])
    resp = ctx["op"].post(f"/pedidos/{pid}/converter")
    assert resp.status_code == 409
    assert "insuficiente" in resp.get_json()["error"].lower()
    assert _peso_azul() == 10
    p = _pedido_db(pid)
    assert p["status"] == "orcamento"
    assert p["comissao_valor"] == 0


def test_converter_e_atomico_com_varias_cores(ctx):
    # Azul cabe, Verde não existe: nada pode ser baixado.
    pid = _orcamento(ctx, itens=[{"cor": "Azul", "peso": "3"}, {"cor": "Verde", "peso": "1"}])
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code == 409
    assert _peso_azul() == 10
    assert _pedido_db(pid)["status"] == "orcamento"


def test_converter_soma_linhas_da_mesma_cor(ctx):
    pid = _orcamento(ctx, itens=[{"cor": "Azul", "peso": "6"}, {"cor": "Azul", "peso": "5"}])
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code == 409
    assert _peso_azul() == 10


def test_converter_duas_vezes_da_409_sem_baixar_de_novo(ctx):
    pid = _orcamento(ctx)
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code == 200
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code == 409
    assert _peso_azul() == 6


def test_converter_pedido_normal_da_409(ctx):
    pid = _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}], False).get_json()["id"]
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code == 409
    assert _peso_azul() == 9


# ---------------------------------------------------------------------------
# B.1) Conversão respeita a intenção gravada (venda casada não baixa)
# ---------------------------------------------------------------------------
def test_converter_venda_casada_nao_baixa_e_sem_409(ctx):
    # Peso muito acima do estoque e tecido inexistente: se tentasse baixar,
    # daria 409. Com descontar_estoque=False, a conversão nem tenta.
    pid = _orcamento(ctx, itens=[{"cor": "Azul", "peso": "999"}], descontar_estoque=False)
    assert _pedido_db(pid)["descontar_estoque"] == 0

    resp = ctx["op"].post(f"/pedidos/{pid}/converter")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["status"] == "pedido"
    assert _peso_azul() == 10
    assert _pedido_db(pid)["status"] == "pedido"


def test_converter_venda_casada_com_tecido_nao_cadastrado(ctx):
    pid = _orcamento(ctx, cliente="op", descontar_estoque=False)
    # Corrige o tecido do orçamento para um que não existe no estoque.
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET tecido = 'Tecido Nao Rastreado' WHERE id = ?", (pid,))
    conn.commit()
    conn.close()

    resp = ctx["op"].post(f"/pedidos/{pid}/converter")
    assert resp.status_code == 200, resp.get_json()
    assert _peso_azul() == 10


def test_converter_com_descontar_true_baixa_e_da_409_se_faltar(ctx):
    pid = _orcamento(ctx, descontar_estoque=True)
    assert _pedido_db(pid)["descontar_estoque"] == 1
    resp = ctx["op"].post(f"/pedidos/{pid}/converter")
    assert resp.status_code == 200, resp.get_json()
    assert _peso_azul() == 6

    pid2 = _orcamento(ctx, itens=[{"cor": "Azul", "peso": "999"}], descontar_estoque=True)
    resp2 = ctx["op"].post(f"/pedidos/{pid2}/converter")
    assert resp2.status_code == 409
    assert _peso_azul() == 6


def test_coluna_descontar_estoque_persiste_pedido_e_orcamento(ctx):
    pid_true = _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}], False, descontar_estoque=True).get_json()["id"]
    assert _pedido_db(pid_true)["descontar_estoque"] == 1

    pid_false = _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}], False, descontar_estoque=False).get_json()["id"]
    assert _pedido_db(pid_false)["descontar_estoque"] == 0

    oid_true = _orcamento(ctx, descontar_estoque=True)
    assert _pedido_db(oid_true)["descontar_estoque"] == 1

    oid_false = _orcamento(ctx, descontar_estoque=False)
    assert _pedido_db(oid_false)["descontar_estoque"] == 0


def test_converter_permissoes_e_isolamento(ctx):
    pid = _orcamento(ctx)
    assert ctx["anon"].post(f"/pedidos/{pid}/converter").status_code == 401
    # Outra loja não enxerga o orçamento (nem baixa o estoque de ninguém).
    assert ctx["outra_admin"].post(f"/pedidos/{pid}/converter").status_code == 404
    assert _peso_azul() == 10
    assert _peso_azul(OUTRA) == 10
    assert ctx["op"].post("/pedidos/99999/converter").status_code == 404
    # Todo papel vende, então todo papel converte.
    assert ctx["gerente"].post(f"/pedidos/{pid}/converter").status_code == 200


# ---------------------------------------------------------------------------
# C) Estoque mínimo
# ---------------------------------------------------------------------------
def _cor_api(c, nome="Azul"):
    estoque = c.get("/api/estoque").get_json()
    return next(cor for t in estoque for cor in t["cores"] if cor["nome_cor"] == nome)


def _set_min(c, cor_id, valor):
    return c.put(f"/api/estoque/cores/{cor_id}/minimo", json={"estoque_minimo": valor})


def test_sem_minimo_nao_alerta(ctx):
    cor = _cor_api(ctx["op"])
    assert cor["estoque_minimo"] == 0
    assert cor["abaixo_minimo"] is False


def test_flag_de_minimo(ctx):
    # Igual ao mínimo já alerta (peso_kg <= estoque_minimo).
    assert _set_min(ctx["gerente"], ctx["cor_id"], "10").status_code == 200
    cor = _cor_api(ctx["op"])
    assert cor["estoque_minimo"] == 10
    assert cor["abaixo_minimo"] is True

    assert _set_min(ctx["admin"], ctx["cor_id"], "9,5").status_code == 200
    assert _cor_api(ctx["op"])["abaixo_minimo"] is False

    # A venda leva o peso para baixo do mínimo: o alerta dispara.
    _criar(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}], False)
    assert _cor_api(ctx["op"])["abaixo_minimo"] is True

    # Mínimo 0 desliga o alerta.
    assert _set_min(ctx["admin"], ctx["cor_id"], "0").status_code == 200
    assert _cor_api(ctx["op"])["abaixo_minimo"] is False


def test_orcamento_nao_dispara_alerta_mas_conversao_sim(ctx):
    _set_min(ctx["admin"], ctx["cor_id"], "7")
    pid = _orcamento(ctx)
    assert _cor_api(ctx["op"])["abaixo_minimo"] is False
    ctx["op"].post(f"/pedidos/{pid}/converter")
    assert _cor_api(ctx["op"])["abaixo_minimo"] is True


def test_definir_minimo_permissoes_e_validacao(ctx):
    assert _set_min(ctx["op"], ctx["cor_id"], "5").status_code == 403
    assert _set_min(ctx["anon"], ctx["cor_id"], "5").status_code == 401
    assert _set_min(ctx["admin"], ctx["cor_id"], "-1").status_code == 400
    assert _set_min(ctx["admin"], ctx["cor_id"], None).status_code == 400
    assert _set_min(ctx["admin"], 99999, "5").status_code == 404
    # Cor de outra loja: 404 e nada muda lá.
    assert _set_min(ctx["admin"], ctx["cor_id_outra"], "50").status_code == 404
    assert _cor_api(ctx["outra_admin"])["estoque_minimo"] == 0
    assert _cor_api(ctx["op"])["estoque_minimo"] == 0


# ---------------------------------------------------------------------------
# D) Migração de banco antigo
# ---------------------------------------------------------------------------
def test_init_db_migra_banco_sem_as_colunas_novas(tmp_path, monkeypatch):
    db = tmp_path / "antigo.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE pedidos (id INTEGER PRIMARY KEY, loja TEXT, total REAL)")
    conn.execute("INSERT INTO pedidos (loja, total) VALUES ('X', 10)")
    conn.execute("CREATE TABLE estoque_cores (id INTEGER PRIMARY KEY, tecido_id INTEGER, nome_cor TEXT, peso_kg REAL)")
    conn.execute("INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg) VALUES (1, 'Azul', 3)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DB_PATH", str(db))
    database.init_db()

    conn = database.get_conn()
    assert conn.execute("SELECT status FROM pedidos").fetchone()["status"] == "pedido"
    assert conn.execute("SELECT estoque_minimo FROM estoque_cores").fetchone()["estoque_minimo"] == 0
    assert conn.execute("SELECT descontar_estoque FROM pedidos").fetchone()["descontar_estoque"] == 1
    conn.close()
