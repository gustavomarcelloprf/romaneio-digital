# Testes de isolamento entre lojas (multi-tenant).
# Cria duas lojas (A e B) e garante que a loja B não consegue
# ler/alterar/apagar dados vinculados à loja A.
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

# app.py exige SECRET_KEY e ADMIN_TOKEN no import (boot fail-fast).
# Valores de teste definidos ANTES do import para a suíte rodar hermética,
# sem depender de variáveis exportadas no shell.
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

from app import app  # noqa: E402


@pytest.fixture()
def dados():
    """Banco limpo com loja A e loja B e dados básicos da loja A."""
    conn = database.get_conn()
    cur = conn.cursor()
    for codigo in ("loja_a", "loja_b"):
        cur.execute(
            "INSERT INTO lojas (codigo, nome, status) VALUES (%s, %s, 'aprovado')",
            (codigo, codigo),
        )
        # Cada loja precisa de um usuário: a sessão agora carrega usuario_id
        # e require_login revalida o usuário a cada request.
        cur.execute(
            "INSERT INTO usuarios (loja, nome, login, senha_hash, papel) VALUES (%s, 'Dono', 'dono', 'x', 'admin')",
            (codigo,),
        )
    cur.execute("INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Malha', 'loja_a') RETURNING id")
    tecido_a = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (%s, 'Azul', 10, 5) RETURNING id",
        (tecido_a,),
    )
    cor_a = cur.fetchone()["id"]
    cur.execute("INSERT INTO clientes (nome, loja) VALUES ('Cliente A', 'loja_a') RETURNING id")
    cliente_a = cur.fetchone()["id"]
    cur.execute("INSERT INTO clientes (nome, loja) VALUES ('Cliente B', 'loja_b') RETURNING id")
    cliente_b = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    yield {
        "tecido_a": tecido_a,
        "cor_a": cor_a,
        "cliente_a": cliente_a,
        "cliente_b": cliente_b,
    }


def _client(loja):
    conn = database.get_conn()
    usuario = conn.execute(
        "SELECT id, papel FROM usuarios WHERE loja = %s LIMIT 1", (loja,)
    ).fetchone()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["loja_codigo"] = loja
        s["usuario_id"] = usuario["id"]
        s["papel"] = usuario["papel"]
    return c


def _cor_existe(cor_id):
    conn = database.get_conn()
    row = conn.execute("SELECT peso_kg FROM estoque_cores WHERE id = %s", (cor_id,)).fetchone()
    conn.close()
    return row


def test_loja_b_nao_deleta_cor_da_loja_a(dados):
    resp = _client("loja_b").delete(f"/api/estoque/cores/{dados['cor_a']}")
    assert resp.status_code == 404
    assert _cor_existe(dados["cor_a"]) is not None


def test_loja_a_deleta_propria_cor(dados):
    resp = _client("loja_a").delete(f"/api/estoque/cores/{dados['cor_a']}")
    assert resp.status_code == 200
    assert _cor_existe(dados["cor_a"]) is None


def test_loja_b_nao_atualiza_cor_da_loja_a(dados):
    resp = _client("loja_b").put(
        f"/api/estoque/cores/{dados['cor_a']}", json={"peso_kg": 999, "qtd_pecas": 99}
    )
    assert resp.status_code == 404
    assert _cor_existe(dados["cor_a"])["peso_kg"] == 10


def test_loja_a_atualiza_propria_cor(dados):
    resp = _client("loja_a").put(
        f"/api/estoque/cores/{dados['cor_a']}", json={"peso_kg": 20, "qtd_pecas": 8}
    )
    assert resp.status_code == 200
    assert _cor_existe(dados["cor_a"])["peso_kg"] == 20


def test_loja_b_nao_adiciona_cor_em_tecido_da_loja_a(dados):
    resp = _client("loja_b").post(
        "/api/estoque/cores",
        json={"tecido_id": dados["tecido_a"], "nome_cor": "Verde", "peso_kg": 5, "qtd_pecas": 2},
    )
    assert resp.status_code == 404
    assert "Tecido não encontrado nesta loja." in resp.get_json()["error"]


def test_post_pedido_com_cliente_de_outra_loja(dados):
    resp = _client("loja_b").post(
        "/pedidos",
        json={
            "cliente_id": dados["cliente_a"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}],
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Cliente inválido para esta loja."


def test_pedido_grava_usuario_logado_como_vendedor(dados):
    # O vendedor deixou de vir do payload: é sempre o usuário da sessão.
    c = _client("loja_b")
    resp = c.post(
        "/pedidos",
        json={
            "cliente_id": dados["cliente_b"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}],
        },
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    row = conn.execute(
        "SELECT p.usuario_id, u.loja FROM pedidos p JOIN usuarios u ON u.id = p.usuario_id WHERE p.id = %s",
        (resp.get_json()["id"],),
    ).fetchone()
    conn.close()
    assert row["loja"] == "loja_b"


def test_post_pedido_valido_da_propria_loja(dados):
    resp = _client("loja_b").post(
        "/pedidos",
        json={
            "cliente_id": dados["cliente_b"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}],
        },
    )
    assert resp.status_code == 201


def test_put_pedido_com_cliente_de_outra_loja(dados):
    client_b = _client("loja_b")
    criado = client_b.post(
        "/pedidos",
        json={
            "cliente_id": dados["cliente_b"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}],
        },
    )
    assert criado.status_code == 201
    pedido_id = criado.get_json()["id"]

    resp = client_b.put(
        f"/pedidos/{pedido_id}",
        json={
            "cliente_id": dados["cliente_a"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}],
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Cliente inválido para esta loja."


def _contar_itens(pedido_id):
    conn = database.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM itens_pedido WHERE pedido_id = %s", (pedido_id,)
    ).fetchone()["n"]
    conn.close()
    return n


def test_loja_b_nao_edita_pedido_da_loja_a(dados):
    # Pedido da loja A com dois itens.
    criado = _client("loja_a").post(
        "/pedidos",
        json={
            "cliente_id": dados["cliente_a"],
            "preco_unitario": "10,00",
            "tecido": "Malha",
            "itens": [{"cor": "Azul", "peso": "1,0"}, {"cor": "Azul", "peso": "2,0"}],
        },
    )
    assert criado.status_code == 201
    pedido_id = criado.get_json()["id"]
    assert _contar_itens(pedido_id) == 2

    # Loja B tenta sobrescrever o pedido usando um cliente válido da própria
    # loja B (passa nas validações de cliente/vendedor, mas o pedido é da loja A).
    resp = _client("loja_b").put(
        f"/pedidos/{pedido_id}",
        json={
            "cliente_id": dados["cliente_b"],
            "preco_unitario": "99,00",
            "tecido": "Outro",
            "itens": [{"cor": "Verde", "peso": "0,5"}],
        },
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Pedido não encontrado."

    # Os itens originais do pedido da loja A permanecem intactos.
    assert _contar_itens(pedido_id) == 2
    conn = database.get_conn()
    cores = [
        r["cor"]
        for r in conn.execute(
            "SELECT cor FROM itens_pedido WHERE pedido_id = %s ORDER BY id", (pedido_id,)
        ).fetchall()
    ]
    conn.close()
    assert cores == ["Azul", "Azul"]
