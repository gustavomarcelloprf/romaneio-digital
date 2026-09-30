# Testes de GET /pedidos/recentes: a lista "Pedidos recentes" da tela Vender
# mostra só os pedidos DE HOJE do usuário logado, na loja da sessão, os mais
# novos primeiro e no máximo 15.
import os
import sys
from datetime import datetime, timedelta
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

app_module.limiter.enabled = False

LOJA = "Loja Recentes"
OUTRA = "Loja Vizinha"
SENHA = "s3nh4-forte"


def _login(login, loja=LOJA):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": loja, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Duas lojas aprovadas (cada uma com admin), um operador e um cliente na LOJA."""
    c = app.test_client()
    for loja in (LOJA, OUTRA):
        assert c.post(
            "/auth/signup",
            json={"nome_loja": loja, "nome": "Dono", "login": "admin", "senha": SENHA},
        ).status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado'")
    op_id = conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (%s, 'Operador', 'operador', %s, 'operador', 10) RETURNING id",
        (LOJA, generate_password_hash(SENHA)),
    ).fetchone()["id"]
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Maria Cadastrada', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    yield {
        "admin": _login("admin"),
        "op": _login("operador"),
        "outra": _login("admin", loja=OUTRA),
        "op_id": op_id,
        "cliente_id": cliente_id,
    }


def _vender(c, **extra):
    payload = {
        "preco_unitario": "10,00",
        "tecido": "Linho",
        "tecido_avulso": True,
        "itens": [{"cor": "Cru", "peso": "2"}],
    }
    payload.update(extra)
    resp = c.post("/pedidos", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def _mudar_data(pedido_id, data_iso):
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET data_iso = %s WHERE id = %s", (data_iso, pedido_id))
    conn.commit()
    conn.close()


def _recentes(c):
    resp = c.get("/pedidos/recentes")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_exige_login():
    assert app.test_client().get("/pedidos/recentes").status_code == 401


def test_campos_cliente_coalesce_e_pago(ctx):
    cadastrado = _vender(ctx["op"], cliente_id=ctx["cliente_id"], pago=False)
    avulso = _vender(ctx["op"], cliente_avulso="João da Feira")
    sem_cliente = _vender(ctx["op"])

    lista = _recentes(ctx["op"])
    por_id = {p["id"]: p for p in lista}
    assert set(por_id) == {cadastrado, avulso, sem_cliente}

    assert por_id[cadastrado]["cliente_nome"] == "Maria Cadastrada"
    assert por_id[cadastrado]["pago"] == 0
    assert por_id[avulso]["cliente_nome"] == "João da Feira"
    assert por_id[avulso]["pago"] == 1
    assert por_id[sem_cliente]["cliente_nome"] == ""

    p = por_id[avulso]
    assert p["tecido"] == "Linho"
    assert float(p["total"]) == 20.0
    assert {"id", "cliente_nome", "tecido", "total", "pago", "data_iso"} <= set(p)


def test_so_pedidos_do_usuario_logado(ctx):
    do_op = _vender(ctx["op"])
    do_admin = _vender(ctx["admin"])

    assert [p["id"] for p in _recentes(ctx["op"])] == [do_op]
    assert [p["id"] for p in _recentes(ctx["admin"])] == [do_admin]


def test_so_pedidos_de_hoje(ctx):
    ontem = _vender(ctx["op"])
    amanha = _vender(ctx["op"])
    hoje = _vender(ctx["op"])
    agora = datetime.now()
    _mudar_data(ontem, (agora - timedelta(days=1)).strftime("%Y-%m-%d 23:59"))
    _mudar_data(amanha, (agora + timedelta(days=1)).strftime("%Y-%m-%d 00:00"))

    assert [p["id"] for p in _recentes(ctx["op"])] == [hoje]


def test_limite_15_mais_novo_no_topo(ctx):
    ids = [_vender(ctx["op"]) for _ in range(17)]

    lista = _recentes(ctx["op"])
    assert len(lista) == 15
    assert [p["id"] for p in lista] == list(reversed(ids))[:15]


def test_isolamento_por_loja(ctx):
    meu = _vender(ctx["op"])
    da_vizinha = _vender(ctx["outra"])

    assert [p["id"] for p in _recentes(ctx["outra"])] == [da_vizinha]
    assert [p["id"] for p in _recentes(ctx["op"])] == [meu]

    # Mesmo com o usuario_id do operador, um pedido de outra loja não vaza:
    # o filtro por loja da sessão vale por si só.
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET usuario_id = %s WHERE id = %s", (ctx["op_id"], da_vizinha))
    conn.commit()
    conn.close()
    assert [p["id"] for p in _recentes(ctx["op"])] == [meu]


def test_pedido_removido_sai_da_lista(ctx):
    fica = _vender(ctx["op"])
    sai = _vender(ctx["admin"])
    # O admin remove o próprio pedido; o do operador continua na lista dele.
    assert ctx["admin"].delete(f"/pedidos/{sai}").status_code == 200
    assert _recentes(ctx["admin"]) == []
    assert [p["id"] for p in _recentes(ctx["op"])] == [fica]
