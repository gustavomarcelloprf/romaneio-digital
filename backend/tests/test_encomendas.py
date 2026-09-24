# Testes de Encomendas: o previsto NUNCA mexe no estoque; receber credita
# o estoque com os pesos REAIS pelo mesmo caminho da entrada, marca a
# encomenda como 'recebida' e devolve a variação previsto x recebido.
import os
import sys
import uuid
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

_TMP_DB = Path(__file__).parent / f"_test_{uuid.uuid4().hex}.db"
database.DB_PATH = str(_TMP_DB)

import app as app_module  # noqa: E402
from app import app  # noqa: E402

app_module.limiter.enabled = False

LOJA = "Loja Encomendas"
SENHA = "s3nh4-forte"


def _login(login):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, gerente e operador; tecido Malha com Azul = 10 kg."""
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


def _criar_encomenda(c, itens=None, fornecedor="Tecelagem Sul", data_prevista="2026-10-01"):
    return c.post(
        "/api/encomendas",
        json={
            "fornecedor": fornecedor,
            "data_prevista": data_prevista,
            "itens": itens or [{"tecido": "Malha", "cor": "Azul", "peso_previsto": "20", "rolos_previstos": 2}],
        },
    )


# ---------------------------------------------------------------------------
# Criação: NUNCA mexe no estoque.
# ---------------------------------------------------------------------------
def test_criar_encomenda_nao_altera_estoque(ctx):
    resp = _criar_encomenda(ctx["gerente"])
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "aberta"
    assert body["fornecedor"] == "Tecelagem Sul"
    assert body["data_prevista"] == "2026-10-01"
    assert body["itens"] == [{
        "id": body["itens"][0]["id"], "tecido": "Malha", "cor": "Azul",
        "peso_previsto": 20.0, "rolos_previstos": 2,
    }]
    # Estoque intacto: continua em 10 kg (o peso previsto não credita nada).
    assert _cor("Azul")["peso_kg"] == 10
    assert _count("entradas") == 0


def test_criar_encomenda_permite_tecido_ainda_nao_cadastrado(ctx):
    # Previsto é só um plano: pode citar tecido/cor que ainda não existem no estoque.
    resp = _criar_encomenda(ctx["admin"], itens=[{"tecido": "Brim", "cor": "Preto", "peso_previsto": "5"}])
    assert resp.status_code == 201, resp.get_json()


def test_criar_encomenda_sem_itens_e_recusada(ctx):
    resp = ctx["admin"].post("/api/encomendas", json={"itens": []})
    assert resp.status_code == 400
    assert _count("encomendas") == 0


def test_criar_encomenda_com_item_invalido_nao_grava_nada(ctx):
    resp = ctx["admin"].post("/api/encomendas", json={"itens": [
        {"tecido": "Malha", "cor": "Azul", "peso_previsto": "10"},
        {"tecido": "Malha", "cor": "", "peso_previsto": "5"},
        {"tecido": "Malha", "cor": "Rosa", "peso_previsto": "0"},
    ]})
    assert resp.status_code == 400
    assert [e["linha"] for e in resp.get_json()["erros"]] == [2, 3]
    assert _count("encomendas") == 0
    assert _count("encomenda_itens") == 0


# ---------------------------------------------------------------------------
# Listagem e detalhe
# ---------------------------------------------------------------------------
def test_lista_e_detalhe_encomenda(ctx):
    criada = _criar_encomenda(ctx["admin"]).get_json()

    lista = ctx["admin"].get("/api/encomendas")
    assert lista.status_code == 200
    assert [e["id"] for e in lista.get_json()] == [criada["id"]]
    assert lista.get_json()[0]["status"] == "aberta"

    detalhe = ctx["admin"].get(f"/api/encomendas/{criada['id']}")
    assert detalhe.status_code == 200
    assert detalhe.get_json()["itens"] == criada["itens"]


def test_detalhe_encomenda_inexistente_e_404(ctx):
    assert ctx["admin"].get("/api/encomendas/999").status_code == 404


def test_lista_filtra_por_status(ctx):
    c1 = _criar_encomenda(ctx["admin"]).get_json()
    _criar_encomenda(ctx["admin"], itens=[{"tecido": "Malha", "cor": "Azul", "peso_previsto": "1"}])
    ctx["admin"].post(f"/api/encomendas/{c1['id']}/receber", json={
        "linhas": [{"item_id": c1["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}],
    })
    abertas = ctx["admin"].get("/api/encomendas?status=aberta").get_json()
    recebidas = ctx["admin"].get("/api/encomendas?status=recebida").get_json()
    assert len(abertas) == 1 and len(recebidas) == 1
    assert recebidas[0]["id"] == c1["id"]


# ---------------------------------------------------------------------------
# Receber: credita o estoque com o peso REAL, marca 'recebida', devolve variação.
# ---------------------------------------------------------------------------
def test_receber_credita_estoque_com_peso_real_e_marca_recebida(ctx):
    criada = _criar_encomenda(ctx["gerente"], itens=[
        {"tecido": "Malha", "cor": "Azul", "peso_previsto": "20", "rolos_previstos": 2},
        {"tecido": "Malha", "cor": "Vermelho", "peso_previsto": "5"},
    ]).get_json()
    item_azul, item_vermelho = criada["itens"]

    resp = ctx["gerente"].post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [
            # Peso real diferente do previsto (18,5 em vez de 20).
            {"item_id": item_azul["id"], "tecido": "Malha", "cor": "Azul", "peso": "18,5", "id_rolo": "R-1"},
            {"item_id": item_vermelho["id"], "tecido": "Malha", "cor": "Vermelho", "peso": "6"},
        ],
    })
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "recebida"
    assert body["linhas"] == 2
    assert body["peso_total"] == 24.5
    assert body["rolos"] == 1
    assert body["cores_criadas"] == [{"tecido": "Malha", "cor": "Vermelho"}]

    variacao = {v["item_id"]: v for v in body["variacao"]}
    assert variacao[item_azul["id"]]["peso_previsto"] == 20.0
    assert variacao[item_azul["id"]]["peso_recebido"] == 18.5
    assert variacao[item_azul["id"]]["variacao"] == -1.5
    assert variacao[item_vermelho["id"]]["peso_previsto"] == 5.0
    assert variacao[item_vermelho["id"]]["peso_recebido"] == 6.0
    assert variacao[item_vermelho["id"]]["variacao"] == 1.0

    # Estoque creditado com o peso REAL (não o previsto).
    assert _cor("Azul")["peso_kg"] == 28.5
    assert _cor("Vermelho")["peso_kg"] == 6.0

    # Reaproveitou o mecanismo de entrada: virou uma linha em `entradas` + rolo.
    assert _count("entradas") == 1
    conn = database.get_conn()
    entrada = conn.execute("SELECT loja, fornecedor FROM entradas").fetchone()
    rolo = conn.execute("SELECT roll_ext_id, peso FROM rolos").fetchone()
    conn.close()
    assert dict(entrada) == {"loja": LOJA, "fornecedor": "Tecelagem Sul"}
    assert dict(rolo) == {"roll_ext_id": "R-1", "peso": 18.5}

    status_final = ctx["gerente"].get(f"/api/encomendas/{criada['id']}").get_json()
    assert status_final["status"] == "recebida"


def test_receber_item_nao_entregue_aparece_com_variacao_negativa(ctx):
    criada = _criar_encomenda(ctx["admin"], itens=[
        {"tecido": "Malha", "cor": "Azul", "peso_previsto": "20"},
        {"tecido": "Malha", "cor": "Vermelho", "peso_previsto": "5"},
    ]).get_json()
    item_azul, item_vermelho = criada["itens"]

    resp = ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [{"item_id": item_azul["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}],
    })
    assert resp.status_code == 201
    variacao = {v["item_id"]: v for v in resp.get_json()["variacao"]}
    assert variacao[item_vermelho["id"]]["peso_recebido"] == 0
    assert variacao[item_vermelho["id"]]["variacao"] == -5.0


def test_receber_encomenda_ja_recebida_e_recusado(ctx):
    criada = _criar_encomenda(ctx["admin"]).get_json()
    linhas = {"linhas": [{"item_id": criada["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}]}
    assert ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json=linhas).status_code == 201
    resp = ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json=linhas)
    assert resp.status_code == 409
    assert _cor("Azul")["peso_kg"] == 30  # não creditou duas vezes


def test_receber_encomenda_inexistente_e_404(ctx):
    resp = ctx["admin"].post("/api/encomendas/999/receber", json={"linhas": []})
    assert resp.status_code == 404


def test_receber_com_tecido_nao_cadastrado_e_recusado(ctx):
    criada = _criar_encomenda(ctx["admin"], itens=[{"tecido": "Brim", "cor": "Preto", "peso_previsto": "5"}]).get_json()
    resp = ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [{"item_id": criada["itens"][0]["id"], "tecido": "Brim", "cor": "Preto", "peso": "5"}],
    })
    assert resp.status_code == 400
    assert "Brim" in resp.get_json()["error"]
    assert _count("entradas") == 0
    status = ctx["admin"].get(f"/api/encomendas/{criada['id']}").get_json()
    assert status["status"] == "aberta"


def test_receber_sem_linhas_e_recusado(ctx):
    criada = _criar_encomenda(ctx["admin"]).get_json()
    resp = ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json={"linhas": []})
    assert resp.status_code == 400


def test_receber_com_item_id_de_outra_encomenda_e_recusado(ctx):
    c1 = _criar_encomenda(ctx["admin"]).get_json()
    c2 = _criar_encomenda(ctx["admin"]).get_json()
    resp = ctx["admin"].post(f"/api/encomendas/{c1['id']}/receber", json={
        "linhas": [{"item_id": c2["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}],
    })
    assert resp.status_code == 400
    assert _count("entradas") == 0


def test_receber_permite_linha_extra_sem_item_id(ctx):
    # Linha fora do previsto (não faz parte da variação, mas credita o estoque igual).
    criada = _criar_encomenda(ctx["admin"]).get_json()
    resp = ctx["admin"].post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [
            {"item_id": criada["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"},
            {"tecido": "Malha", "cor": "Vermelho", "peso": "3"},
        ],
    })
    assert resp.status_code == 201, resp.get_json()
    assert _cor("Vermelho")["peso_kg"] == 3.0
    assert len(resp.get_json()["variacao"]) == 1


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------
def test_operador_recebe_403_em_todas_as_rotas(ctx):
    op = ctx["op"]
    assert _criar_encomenda(op).status_code == 403
    criada = _criar_encomenda(ctx["admin"]).get_json()
    assert op.get("/api/encomendas").status_code == 403
    assert op.get(f"/api/encomendas/{criada['id']}").status_code == 403
    assert op.post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [{"item_id": criada["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}],
    }).status_code == 403
    assert _cor("Azul")["peso_kg"] == 10


@pytest.mark.parametrize("quem", ["gerente", "admin"])
def test_gerente_e_admin_usam_todas_as_rotas(ctx, quem):
    resp = _criar_encomenda(ctx[quem])
    assert resp.status_code == 201
    criada = resp.get_json()
    assert ctx[quem].get("/api/encomendas").status_code == 200
    assert ctx[quem].get(f"/api/encomendas/{criada['id']}").status_code == 200
    recebe = ctx[quem].post(f"/api/encomendas/{criada['id']}/receber", json={
        "linhas": [{"item_id": criada["itens"][0]["id"], "tecido": "Malha", "cor": "Azul", "peso": "20"}],
    })
    assert recebe.status_code == 201


def test_encomendas_exige_login():
    c = app.test_client()
    assert c.post("/api/encomendas", json={"itens": []}).status_code == 401
    assert c.get("/api/encomendas").status_code == 401
    assert c.get("/api/encomendas/1").status_code == 401
    assert c.post("/api/encomendas/1/receber", json={"linhas": []}).status_code == 401


def test_encomenda_isolada_por_loja(ctx):
    conn = database.get_conn()
    conn.execute("INSERT INTO lojas (codigo, nome, status) VALUES ('Outra', 'Outra', 'aprovado')")
    outro_id = conn.execute(
        "INSERT INTO encomendas (loja, fornecedor, status) VALUES ('Outra', 'Fornecedor Y', 'aberta')"
    ).lastrowid
    conn.commit()
    conn.close()
    assert ctx["admin"].get(f"/api/encomendas/{outro_id}").status_code == 404
    assert [e["id"] for e in ctx["admin"].get("/api/encomendas").get_json()] == []
