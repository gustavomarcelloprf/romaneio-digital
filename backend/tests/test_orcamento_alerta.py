# Testes do ALERTA de estoque mínimo por cor. (O orçamento que vivia aqui foi
# aposentado: a rota /converter não existe mais; ver test_venda_simplificada.py.)
import os
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

# app.py exige SECRET_KEY e ADMIN_TOKEN no import (boot fail-fast).
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

import app as app_module  # noqa: E402
from app import app  # noqa: E402

# Rate limit do /auth/login desligado só nos testes (a suíte loga muitas vezes).
app_module.limiter.enabled = False

LOJA = "Loja Alerta"
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
    c = app.test_client()
    for loja in (LOJA, OUTRA):
        assert c.post(
            "/auth/signup",
            json={"nome_loja": loja, "nome": "Dono", "login": "admin", "senha": SENHA},
        ).status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado'")
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente X', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel) VALUES (%s, 'Gerente', 'gerente', %s, 'gerente')",
        (LOJA, generate_password_hash(SENHA)),
    )
    op_id = conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) VALUES (%s, 'Operador', 'operador', %s, 'operador', 10) RETURNING id",
        (LOJA, generate_password_hash(SENHA)),
    ).fetchone()["id"]
    cor_ids = {}
    for loja in (LOJA, OUTRA):
        tecido_id = conn.execute(
            "INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Malha', %s) RETURNING id", (loja,)
        ).fetchone()["id"]
        cor_ids[loja] = conn.execute(
            "INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (%s, 'Azul', 10, 3) RETURNING id",
            (tecido_id,),
        ).fetchone()["id"]
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


def _peso_azul(loja=LOJA):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT c.peso_kg FROM estoque_cores c JOIN estoque_tecidos t ON t.id = c.tecido_id "
        "WHERE t.loja = %s AND t.nome_tecido = 'Malha' AND c.nome_cor = 'Azul'",
        (loja,),
    ).fetchone()
    conn.close()
    return row["peso_kg"]


def _vender(c, cliente_id, itens, descontar_estoque=True):
    """Venda a R$ 10/kg."""
    return c.post(
        "/pedidos",
        json={
            "cliente_id": cliente_id,
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": itens,
            "descontar_estoque": descontar_estoque,
        },
    )


# ---------------------------------------------------------------------------
# A) Estoque mínimo
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
    _vender(ctx["op"], ctx["cliente_id"], [{"cor": "Azul", "peso": "1"}])
    assert _cor_api(ctx["op"])["abaixo_minimo"] is True

    # Mínimo 0 desliga o alerta.
    assert _set_min(ctx["admin"], ctx["cor_id"], "0").status_code == 200
    assert _cor_api(ctx["op"])["abaixo_minimo"] is False


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
# B) Migração de banco antigo
# ---------------------------------------------------------------------------
def test_init_db_migra_banco_sem_as_colunas_novas(banco_sem_schema):
    # Schema "antigo" montado à mão: pedidos e estoque_cores sem as colunas
    # novas, já com linhas. init_db() precisa acrescentá-las preenchidas.
    conn = database.get_conn()
    conn.execute(
        "CREATE TABLE pedidos (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "loja TEXT, total DOUBLE PRECISION)"
    )
    conn.execute("INSERT INTO pedidos (loja, total) VALUES ('X', 10)")
    conn.execute(
        "CREATE TABLE estoque_cores (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "tecido_id INTEGER, nome_cor TEXT, peso_kg DOUBLE PRECISION)"
    )
    conn.execute("INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg) VALUES (1, 'Azul', 3)")
    conn.commit()
    conn.close()

    database.init_db()

    conn = database.get_conn()
    assert conn.execute("SELECT status FROM pedidos").fetchone()["status"] == "pedido"
    assert conn.execute("SELECT estoque_minimo FROM estoque_cores").fetchone()["estoque_minimo"] == 0
    assert conn.execute("SELECT descontar_estoque FROM pedidos").fetchone()["descontar_estoque"] == 1
    assert conn.execute("SELECT cliente_avulso FROM pedidos").fetchone()["cliente_avulso"] is None
    conn.close()
