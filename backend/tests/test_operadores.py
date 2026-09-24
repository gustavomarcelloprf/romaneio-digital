# Testes de operadores e comissão (Fase 1, passo 2): gestão de usuários
# admin-only, vendedor = usuário logado e comissão congelada no pedido.
import os
import sys
from pathlib import Path

import pytest

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

LOJA = "Loja Comissao"
SENHA = "s3nh4-forte"


@pytest.fixture()
def ctx():
    """Banco limpo com loja aprovada, admin logado e um cliente."""
    c = app.test_client()
    resp = c.post(
        "/auth/signup",
        json={"nome_loja": LOJA, "nome": "Admin da Loja", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=%s", (LOJA,))
    cur = conn.execute("INSERT INTO clientes (nome, loja) VALUES ('Cliente X', %s) RETURNING id", (LOJA,))
    cliente_id = cur.fetchone()["id"]
    admin_id = conn.execute(
        "SELECT id FROM usuarios WHERE loja=%s AND login='admin'", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": "admin", "senha": SENHA})
    assert resp.status_code == 200

    yield {"admin": c, "cliente_id": cliente_id, "admin_id": admin_id}


def _criar_operador(admin_client, login="operador", taxa="5"):
    # O operador nasce sem senha; aceita o convite para poder logar.
    resp = admin_client.post(
        "/usuarios",
        json={"nome": "Operador Um", "login": login, "taxa_comissao": taxa},
    )
    if resp.status_code == 201:
        aceite = app.test_client().post(resp.get_json()["convite_path"], json={"senha": SENHA})
        assert aceite.status_code == 200
    return resp


def _login_operador(login="operador"):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": SENHA})
    assert resp.status_code == 200
    return c


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


def _pedido_db(pedido_id):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT usuario_id, total, comissao_taxa, comissao_valor FROM pedidos WHERE id=%s",
        (pedido_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _set_taxa(usuario_login, taxa):
    conn = database.get_conn()
    conn.execute(
        "UPDATE usuarios SET taxa_comissao=%s WHERE loja=%s AND login=%s",
        (taxa, LOJA, usuario_login),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Gestão de operadores
# ---------------------------------------------------------------------------
def test_admin_cria_operador_e_lista(ctx):
    resp = _criar_operador(ctx["admin"], taxa="2,5")
    assert resp.status_code == 201
    assert resp.get_json()["taxa_comissao"] == 2.5

    resp = ctx["admin"].get("/usuarios")
    assert resp.status_code == 200
    usuarios = resp.get_json()
    operador = next(u for u in usuarios if u["login"] == "operador")
    assert operador["papel"] == "operador"
    assert operador["taxa_comissao"] == 2.5
    assert operador["ativo"] == 1
    assert all("senha_hash" not in u for u in usuarios)


def test_login_duplicado_na_loja_retorna_409(ctx):
    assert _criar_operador(ctx["admin"]).status_code == 201
    resp = _criar_operador(ctx["admin"])
    assert resp.status_code == 409


def test_operador_recebe_403_na_gestao_de_usuarios(ctx):
    _criar_operador(ctx["admin"])
    op = _login_operador()

    assert op.get("/usuarios").status_code == 403
    assert op.post(
        "/usuarios", json={"nome": "X", "login": "x", "senha": "x"}
    ).status_code == 403
    assert op.put(
        f"/usuarios/{ctx['admin_id']}", json={"taxa_comissao": 99}
    ).status_code == 403


def test_admin_nao_consegue_se_autodesativar(ctx):
    resp = ctx["admin"].put(f"/usuarios/{ctx['admin_id']}", json={"ativo": 0})
    assert resp.status_code == 400

    resp = ctx["admin"].get("/usuarios")
    admin = next(u for u in resp.get_json() if u["id"] == ctx["admin_id"])
    assert admin["ativo"] == 1


def test_admin_desativa_operador(ctx):
    op_id = _criar_operador(ctx["admin"]).get_json()["id"]
    resp = ctx["admin"].put(f"/usuarios/{op_id}", json={"ativo": 0})
    assert resp.status_code == 200
    # Operador desativado não loga mais.
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": "operador", "senha": SENHA})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Comissão nos pedidos
# ---------------------------------------------------------------------------
def test_pedido_grava_usuario_e_comissao_congelada(ctx):
    _criar_operador(ctx["admin"], taxa="5")
    op = _login_operador()

    resp = _post_pedido(op, ctx["cliente_id"])  # total = 1,0 kg * 10,00 = 10,00
    assert resp.status_code == 201
    pedido = _pedido_db(resp.get_json()["id"])

    conn = database.get_conn()
    op_id = conn.execute(
        "SELECT id FROM usuarios WHERE loja=%s AND login='operador'", (LOJA,)
    ).fetchone()["id"]
    conn.close()

    assert pedido["usuario_id"] == op_id
    assert pedido["total"] == 10.0
    assert pedido["comissao_taxa"] == 5.0
    assert pedido["comissao_valor"] == round(10.0 * 5 / 100, 2)  # 0.50


def test_mudanca_de_taxa_nao_afeta_pedido_anterior(ctx):
    _criar_operador(ctx["admin"], taxa="5")
    op = _login_operador()

    antigo_id = _post_pedido(op, ctx["cliente_id"]).get_json()["id"]
    _set_taxa("operador", 10)
    novo_id = _post_pedido(op, ctx["cliente_id"]).get_json()["id"]

    antigo, novo = _pedido_db(antigo_id), _pedido_db(novo_id)
    assert antigo["comissao_taxa"] == 5.0
    assert antigo["comissao_valor"] == 0.50
    assert novo["comissao_taxa"] == 10.0
    assert novo["comissao_valor"] == 1.00


def test_put_pedido_recalcula_com_taxa_congelada(ctx):
    _criar_operador(ctx["admin"], taxa="5")
    op = _login_operador()

    pedido_id = _post_pedido(op, ctx["cliente_id"]).get_json()["id"]
    # Taxa do usuário muda DEPOIS do pedido; a edição deve ignorá-la.
    _set_taxa("operador", 10)

    resp = op.put(
        f"/pedidos/{pedido_id}",
        json={
            "cliente_id": ctx["cliente_id"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "2,0"}],  # novo total = 20,00
        },
    )
    assert resp.status_code == 200

    pedido = _pedido_db(pedido_id)
    assert pedido["total"] == 20.0
    assert pedido["comissao_taxa"] == 5.0  # congelada
    assert pedido["comissao_valor"] == round(20.0 * 5 / 100, 2)  # 1.00, não 2.00


def test_get_pedidos_inclui_vendedor_e_comissao(ctx):
    _criar_operador(ctx["admin"], taxa="5")
    op = _login_operador()
    _post_pedido(op, ctx["cliente_id"])

    resp = op.get("/pedidos")
    assert resp.status_code == 200
    pedido = resp.get_json()[0]
    assert pedido["vendedor_nome"] == "Operador Um"
    assert pedido["comissao_valor"] == 0.50
