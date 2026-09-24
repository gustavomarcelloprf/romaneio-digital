# Testes de fiado: pedido a prazo abre saldo devedor do cliente, pagamentos
# abatem esse saldo, e as rotas de gestão exigem fiado_gerir (gerente/admin).
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

app_module.limiter.enabled = False

LOJA = "Loja Fiado"
SENHA = "s3nh4-forte"


def _login(login):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, operador e gerente, e dois clientes."""
    admin = app.test_client()
    resp = admin.post(
        "/auth/signup",
        json={"nome_loja": LOJA, "nome": "Dono da Loja", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=%s", (LOJA,))
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (%s, 'Gerente Um', 'gerente', %s, 'gerente', 0)",
        (LOJA, generate_password_hash(SENHA)),
    )
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (%s, 'Operador Um', 'operador', %s, 'operador', 0)",
        (LOJA, generate_password_hash(SENHA)),
    )
    cliente_a = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente A', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    cliente_b = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente B', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    admin = _login("admin")

    yield {
        "admin": admin,
        "gerente": _login("gerente"),
        "op": _login("operador"),
        "cliente_a": cliente_a,
        "cliente_b": cliente_b,
    }


def _post_pedido(c, cliente_id, peso="10", preco="10,00", pago=None):
    payload = {
        "cliente_id": cliente_id,
        "preco_unitario": preco,
        "tecido": "Malha",
        "itens": [{"cor": "Azul", "peso": peso}],
    }
    if pago is not None:
        payload["pago"] = pago
    return c.post("/pedidos", json=payload)


def _saldo(c, cliente_id):
    resp = c.get(f"/api/clientes/{cliente_id}/saldo")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["saldo"]


def _pagar(c, cliente_id, valor, data="2026-01-15"):
    return c.post(f"/api/clientes/{cliente_id}/pagamentos", json={"valor": valor, "data": data})


# ---------------------------------------------------------------------------
# Schema / venda à vista por padrão
# ---------------------------------------------------------------------------
def test_pedido_padrao_e_pago_nao_abre_saldo(ctx):
    resp = _post_pedido(ctx["admin"], ctx["cliente_a"])
    assert resp.status_code == 201
    conn = database.get_conn()
    pago = conn.execute("SELECT pago FROM pedidos WHERE id=%s", (resp.get_json()["id"],)).fetchone()["pago"]
    conn.close()
    assert pago == 1
    assert _saldo(ctx["admin"], ctx["cliente_a"]) == 0


# ---------------------------------------------------------------------------
# Pedido fiado abre saldo; pagamento reduz o saldo
# ---------------------------------------------------------------------------
def test_pedido_fiado_abre_saldo(ctx):
    resp = _post_pedido(ctx["admin"], ctx["cliente_a"], peso="10", preco="10,00", pago=False)
    assert resp.status_code == 201
    assert resp.get_json()["total"] == 100.0
    assert _saldo(ctx["admin"], ctx["cliente_a"]) == 100.0


def test_pagamento_reduz_saldo(ctx):
    _post_pedido(ctx["admin"], ctx["cliente_a"], peso="10", preco="10,00", pago=False)  # total 100
    resp = _pagar(ctx["admin"], ctx["cliente_a"], 40)
    assert resp.status_code == 201
    assert resp.get_json()["saldo"] == 60.0
    assert _saldo(ctx["admin"], ctx["cliente_a"]) == 60.0


def test_saldo_com_varios_pedidos_e_pagamentos(ctx):
    admin = ctx["admin"]
    _post_pedido(admin, ctx["cliente_a"], peso="5", preco="10,00", pago=False)   # fiado: 50
    _post_pedido(admin, ctx["cliente_a"], peso="3", preco="10,00", pago=True)    # à vista: não conta
    _post_pedido(admin, ctx["cliente_a"], peso="2", preco="10,00", pago=False)   # fiado: 20
    _pagar(admin, ctx["cliente_a"], 10)
    _pagar(admin, ctx["cliente_a"], 5)
    # (50 + 20) - (10 + 5) = 55
    assert _saldo(admin, ctx["cliente_a"]) == 55.0


def test_pagamento_nao_afeta_outro_cliente(ctx):
    _post_pedido(ctx["admin"], ctx["cliente_a"], peso="10", preco="10,00", pago=False)
    _post_pedido(ctx["admin"], ctx["cliente_b"], peso="5", preco="10,00", pago=False)
    _pagar(ctx["admin"], ctx["cliente_a"], 30)
    assert _saldo(ctx["admin"], ctx["cliente_a"]) == 70.0
    assert _saldo(ctx["admin"], ctx["cliente_b"]) == 50.0


def test_pagamento_rejeita_valor_invalido(ctx):
    resp = _pagar(ctx["admin"], ctx["cliente_a"], 0)
    assert resp.status_code == 400
    resp = _pagar(ctx["admin"], ctx["cliente_a"], -10)
    assert resp.status_code == 400


def test_pagamento_rejeita_cliente_de_outra_loja(ctx):
    admin_b = app.test_client()
    resp = admin_b.post(
        "/auth/signup",
        json={"nome_loja": "Loja Fiado B", "nome": "Outro Dono", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=%s", ("Loja Fiado B",))
    conn.commit()
    conn.close()
    admin_b = app.test_client()
    admin_b.post("/auth/login", json={"nome_loja": "Loja Fiado B", "login": "admin", "senha": SENHA})

    assert _pagar(admin_b, ctx["cliente_a"], 10).status_code == 404
    assert admin_b.get(f"/api/clientes/{ctx['cliente_a']}/saldo").status_code == 404


# ---------------------------------------------------------------------------
# GET /api/fiado: devedores ordenados por saldo desc, mais o total geral
# ---------------------------------------------------------------------------
def test_fiado_lista_devedores_e_total(ctx):
    admin = ctx["admin"]
    _post_pedido(admin, ctx["cliente_a"], peso="10", preco="10,00", pago=False)  # 100
    _post_pedido(admin, ctx["cliente_b"], peso="30", preco="10,00", pago=False)  # 300
    _pagar(admin, ctx["cliente_b"], 100)  # cliente_b: 200

    resp = admin.get("/api/fiado")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["devedores"] == [
        {"cliente_id": ctx["cliente_b"], "nome": "Cliente B", "saldo": 200.0},
        {"cliente_id": ctx["cliente_a"], "nome": "Cliente A", "saldo": 100.0},
    ]
    assert body["total"] == 300.0


def test_fiado_nao_lista_cliente_quitado(ctx):
    admin = ctx["admin"]
    _post_pedido(admin, ctx["cliente_a"], peso="10", preco="10,00", pago=False)  # 100
    _pagar(admin, ctx["cliente_a"], 100)
    body = admin.get("/api/fiado").get_json()
    assert body["devedores"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Marcar pedido fiado como quitado (opcional)
# ---------------------------------------------------------------------------
def test_quitar_pedido_zera_saldo(ctx):
    admin = ctx["admin"]
    pedido_id = _post_pedido(admin, ctx["cliente_a"], peso="10", preco="10,00", pago=False).get_json()["id"]
    assert _saldo(admin, ctx["cliente_a"]) == 100.0
    resp = admin.post(f"/pedidos/{pedido_id}/quitar")
    assert resp.status_code == 200
    assert _saldo(admin, ctx["cliente_a"]) == 0.0


# ---------------------------------------------------------------------------
# Permissões: operador recebe 403; admin e gerente conseguem
# ---------------------------------------------------------------------------
def test_operador_recebe_403_em_pagamentos_saldo_fiado(ctx):
    op = ctx["op"]
    assert _pagar(op, ctx["cliente_a"], 10).status_code == 403
    assert op.get(f"/api/clientes/{ctx['cliente_a']}/saldo").status_code == 403
    assert op.get("/api/fiado").status_code == 403
    assert op.post(f"/pedidos/1/quitar").status_code == 403


@pytest.mark.parametrize("papel", ["admin", "gerente"])
def test_admin_e_gerente_conseguem_gerir_fiado(ctx, papel):
    c = ctx[papel]
    _post_pedido(ctx["admin"], ctx["cliente_a"], peso="10", preco="10,00", pago=False)
    assert c.get(f"/api/clientes/{ctx['cliente_a']}/saldo").status_code == 200
    assert c.get("/api/fiado").status_code == 200
    assert _pagar(c, ctx["cliente_a"], 10).status_code == 201


def test_operador_continua_vendendo_fiado(ctx):
    # Vender fiado é dentro do POST /pedidos, liberado a quem vende.
    resp = _post_pedido(ctx["op"], ctx["cliente_a"], peso="10", preco="10,00", pago=False)
    assert resp.status_code == 201
