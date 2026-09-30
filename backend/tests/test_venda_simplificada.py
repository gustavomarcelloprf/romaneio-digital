# Testes da tela Vender simplificada: parser numérico único ("2.5" e "2,5"
# são 2,5), total sem desconto, fim do orçamento, cliente opcional/avulso
# (fiado exige cadastrado) e tecido "Outro" que não baixa estoque.
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
from app import app, _parse_num  # noqa: E402

app_module.limiter.enabled = False

LOJA = "Loja Venda"
OUTRA = "Loja Outra"
SENHA = "s3nh4-forte"


def _login(login, loja=LOJA):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": loja, "login": login, "senha": SENHA})
    assert resp.status_code == 200, resp.get_json()
    return c


@pytest.fixture()
def ctx():
    """Loja aprovada com admin e operador (taxa 10%), um cliente e Malha/Azul = 10 kg."""
    c = app.test_client()
    for loja in (LOJA, OUTRA):
        assert c.post(
            "/auth/signup",
            json={"nome_loja": loja, "nome": "Dono", "login": "admin", "senha": SENHA},
        ).status_code == 201

    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado'")
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (%s, 'Operador', 'operador', %s, 'operador', 10)",
        (LOJA, generate_password_hash(SENHA)),
    )
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, telefone, loja) VALUES ('Maria Cadastrada', '11999990000', %s) RETURNING id",
        (LOJA,),
    ).fetchone()["id"]
    cliente_outra = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente da Vizinha', %s) RETURNING id", (OUTRA,)
    ).fetchone()["id"]
    tecido_id = conn.execute(
        "INSERT INTO estoque_tecidos (nome_tecido, loja) VALUES ('Malha', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO estoque_cores (tecido_id, nome_cor, peso_kg, qtd_pecas) VALUES (%s, 'Azul', 10, 1)",
        (tecido_id,),
    )
    conn.commit()
    conn.close()

    yield {
        "admin": _login("admin"),
        "op": _login("operador"),
        "cliente_id": cliente_id,
        "cliente_outra": cliente_outra,
    }


def _vender(c, **extra):
    payload = {
        "preco_unitario": "10,00",
        "tecido": "Malha",
        "itens": [{"cor": "Azul", "peso": "2"}],
        "descontar_estoque": True,
    }
    payload.update(extra)
    return c.post("/pedidos", json=payload)


def _peso_azul():
    conn = database.get_conn()
    row = conn.execute(
        "SELECT c.peso_kg FROM estoque_cores c JOIN estoque_tecidos t ON t.id = c.tecido_id "
        "WHERE t.loja = %s AND t.nome_tecido = 'Malha' AND c.nome_cor = 'Azul'",
        (LOJA,),
    ).fetchone()
    conn.close()
    return row["peso_kg"]


def _pedido_db(pedido_id):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT cliente_id, cliente_avulso, total, desconto, status, pago, descontar_estoque "
        "FROM pedidos WHERE id = %s",
        (pedido_id,),
    ).fetchone()
    peso = conn.execute(
        "SELECT COALESCE(SUM(peso_kg), 0) AS p FROM itens_pedido WHERE pedido_id = %s", (pedido_id,)
    ).fetchone()["p"]
    conn.close()
    return {**dict(row), "peso_itens": peso}


# ---------------------------------------------------------------------------
# A) Parser numérico único
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entrada, esperado", [
    ("2.5", 2.5),
    ("2,5", 2.5),
    ("1.234,56", 1234.56),
    ("1234.56", 1234.56),
    ("28.90", 28.90),
    ("28,90", 28.90),
    ("R$ 1.234,56", 1234.56),
    ("10", 10.0),
    (3, 3.0),
    (2.75, 2.75),
])
def test_parse_num_formatos(entrada, esperado):
    assert _parse_num(entrada) == pytest.approx(esperado)


@pytest.mark.parametrize("entrada", [None, "", "abc", True, False])
def test_parse_num_invalidos(entrada):
    assert _parse_num(entrada) is None


@pytest.mark.parametrize("peso, preco, total", [
    ("2.5", "10", 25.0),        # antes: "2.5" virava 25 kg -> R$ 250
    ("2,5", "10", 25.0),
    ("1", "28.90", 28.90),      # antes: "28.90" virava R$ 2890
    ("1", "1.234,56", 1234.56),
    ("1", "1234.56", 1234.56),
])
def test_venda_respeita_decimal_com_ponto_e_virgula(ctx, peso, preco, total):
    resp = _vender(ctx["op"], preco_unitario=preco,
                   itens=[{"cor": "Azul", "peso": peso}], descontar_estoque=False)
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["total"] == pytest.approx(total)
    p = _pedido_db(resp.get_json()["id"])
    assert p["total"] == pytest.approx(total)
    assert p["peso_itens"] == pytest.approx(_parse_num(peso))


def test_baixa_de_estoque_usa_peso_com_ponto_decimal(ctx):
    assert _vender(ctx["op"], itens=[{"cor": "Azul", "peso": "2.5"}]).status_code == 201
    assert _peso_azul() == pytest.approx(7.5)


def test_edicao_usa_o_mesmo_parser(ctx):
    pid = _vender(ctx["op"], descontar_estoque=False).get_json()["id"]
    resp = ctx["op"].put(f"/pedidos/{pid}", json={
        "cliente_id": ctx["cliente_id"], "tecido": "Malha", "preco_unitario": "28.90",
        "itens": [{"cor": "Azul", "peso": "2.5"}],
    })
    assert resp.status_code == 200, resp.get_json()
    p = _pedido_db(pid)
    assert p["total"] == pytest.approx(72.25)
    assert p["peso_itens"] == pytest.approx(2.5)


def test_peso_invalido_da_400(ctx):
    resp = _vender(ctx["op"], itens=[{"cor": "Azul", "peso": "abc"}])
    assert resp.status_code == 400
    assert _peso_azul() == 10


# ---------------------------------------------------------------------------
# B) Sem desconto: total = peso × preço
# ---------------------------------------------------------------------------
def test_total_sem_desconto_mesmo_se_enviado(ctx):
    resp = _vender(ctx["op"], desconto="5,00", itens=[{"cor": "Azul", "peso": "3"}])
    assert resp.status_code == 201
    assert resp.get_json()["total"] == 30.0
    p = _pedido_db(resp.get_json()["id"])
    assert p["total"] == 30.0
    assert p["desconto"] == 0


def test_edicao_ignora_desconto_e_zera_o_legado(ctx):
    pid = _vender(ctx["op"], descontar_estoque=False).get_json()["id"]
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET desconto = 3 WHERE id = %s", (pid,))
    conn.commit(); conn.close()
    resp = ctx["op"].put(f"/pedidos/{pid}", json={
        "cliente_avulso": "João", "tecido": "Malha", "preco_unitario": "10",
        "desconto": "4", "itens": [{"cor": "Azul", "peso": "2"}],
    })
    assert resp.status_code == 200
    p = _pedido_db(pid)
    assert p["total"] == 20.0 and p["desconto"] == 0


def test_romaneio_sem_desconto_renderiza(ctx):
    pid = _vender(ctx["op"], cliente_avulso="João Avulso").get_json()["id"]
    png = ctx["op"].get(f"/exportar/{pid}?type=png")
    assert png.status_code == 200 and png.mimetype == "image/png"


# ---------------------------------------------------------------------------
# C) Orçamento aposentado
# ---------------------------------------------------------------------------
def test_rota_converter_nao_existe_mais(ctx):
    pid = _vender(ctx["op"], cliente_id=ctx["cliente_id"]).get_json()["id"]
    assert ctx["op"].post(f"/pedidos/{pid}/converter").status_code in (404, 405)


def test_is_orcamento_e_ignorado_todo_pedido_e_venda(ctx):
    resp = _vender(ctx["op"], cliente_id=ctx["cliente_id"], is_orcamento=True)
    assert resp.status_code == 201
    assert "status" not in resp.get_json()
    p = _pedido_db(resp.get_json()["id"])
    assert p["status"] == "pedido"
    # Baixou o estoque como qualquer venda.
    assert _peso_azul() == 8


def test_relatorios_contam_todo_pedido(ctx):
    _vender(ctx["op"], cliente_id=ctx["cliente_id"])
    # Um pedido antigo que ficou como 'orcamento' no banco também conta.
    pid = _vender(ctx["op"], cliente_avulso="Avulso", descontar_estoque=False).get_json()["id"]
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET status = 'orcamento' WHERE id = %s", (pid,))
    conn.commit(); conn.close()

    loja = ctx["admin"].get("/api/relatorio/loja?de=2000-01-01&ate=2999-12-31").get_json()
    assert loja["num_pedidos"] == 2
    assert loja["faturamento_total"] == 40.0
    meu = ctx["op"].get("/api/relatorio/meu?de=2000-01-01&ate=2999-12-31").get_json()
    assert meu["num_pedidos"] == 2
    assert {p["cliente_nome"] for p in meu["ultimos_pedidos"]} == {"Maria Cadastrada", "Avulso"}
    # A listagem não separa mais por status (e ?status= não filtra nada).
    assert len(ctx["op"].get("/pedidos").get_json()) == 2
    assert len(ctx["op"].get("/pedidos?status=orcamento").get_json()) == 2


# ---------------------------------------------------------------------------
# D) Cliente opcional: cadastrado, avulso ou em branco
# ---------------------------------------------------------------------------
def test_venda_com_cliente_cadastrado(ctx):
    resp = _vender(ctx["op"], cliente_id=ctx["cliente_id"], cliente_avulso="ignorado")
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    p = _pedido_db(pid)
    assert p["cliente_id"] == ctx["cliente_id"] and p["cliente_avulso"] is None
    pedido = ctx["op"].get(f"/pedidos/{pid}").get_json()["pedido"]
    assert pedido["cliente_nome"] == "Maria Cadastrada"
    assert pedido["cliente_telefone"] == "11999990000"


def test_venda_com_cliente_avulso(ctx):
    resp = _vender(ctx["op"], cliente_avulso="  João da Feira  ")
    assert resp.status_code == 201, resp.get_json()
    pid = resp.get_json()["id"]
    p = _pedido_db(pid)
    assert p["cliente_id"] is None and p["cliente_avulso"] == "João da Feira"
    assert ctx["op"].get(f"/pedidos/{pid}").get_json()["pedido"]["cliente_nome"] == "João da Feira"
    lista = ctx["op"].get("/pedidos").get_json()
    assert [x["cliente_nome"] for x in lista] == ["João da Feira"]
    assert ctx["op"].get(f"/exportar/{pid}?type=pdf").status_code == 200


def test_venda_sem_cliente(ctx):
    resp = _vender(ctx["op"])
    assert resp.status_code == 201, resp.get_json()
    pid = resp.get_json()["id"]
    p = _pedido_db(pid)
    assert p["cliente_id"] is None and p["cliente_avulso"] is None
    assert ctx["op"].get(f"/pedidos/{pid}").get_json()["pedido"]["cliente_nome"] == ""
    assert ctx["op"].get("/pedidos").get_json()[0]["cliente_nome"] == ""
    assert ctx["op"].get(f"/exportar/{pid}?type=png").status_code == 200


def test_cliente_de_outra_loja_ou_invalido_da_400(ctx):
    assert _vender(ctx["op"], cliente_id=ctx["cliente_outra"]).status_code == 400
    assert _vender(ctx["op"], cliente_id=99999).status_code == 400
    assert _vender(ctx["op"], cliente_id="abc").status_code == 400
    assert _peso_azul() == 10


# ---------------------------------------------------------------------------
# E) Fiado exige cliente cadastrado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("extra", [{"cliente_avulso": "João da Feira"}, {}, {"cliente_id": ""}])
def test_fiado_sem_cliente_cadastrado_da_400(ctx, extra):
    resp = _vender(ctx["op"], pago=False, **extra)
    assert resp.status_code == 400
    assert "cliente cadastrado" in resp.get_json()["error"]
    # Nada gravado, nada baixado.
    assert ctx["op"].get("/pedidos").get_json() == []
    assert _peso_azul() == 10


def test_fiado_com_cliente_cadastrado_ok(ctx):
    resp = _vender(ctx["op"], pago=False, cliente_id=ctx["cliente_id"])
    assert resp.status_code == 201
    assert _pedido_db(resp.get_json()["id"])["pago"] == 0
    saldo = ctx["admin"].get(f"/api/clientes/{ctx['cliente_id']}/saldo").get_json()["saldo"]
    assert saldo == 20.0


def test_edicao_de_fiado_nao_pode_tirar_o_cliente(ctx):
    pid = _vender(ctx["op"], pago=False, cliente_id=ctx["cliente_id"]).get_json()["id"]
    resp = ctx["op"].put(f"/pedidos/{pid}", json={
        "cliente_avulso": "Outro nome", "tecido": "Malha", "preco_unitario": "10",
        "itens": [{"cor": "Azul", "peso": "2"}],
    })
    assert resp.status_code == 400
    assert _pedido_db(pid)["cliente_id"] == ctx["cliente_id"]


# ---------------------------------------------------------------------------
# F) Tecido "Outro" (fora do estoque) não baixa estoque
# ---------------------------------------------------------------------------
def test_tecido_outro_nao_baixa_estoque(ctx):
    # descontar_estoque=True no payload, mas tecido_avulso manda: não baixa,
    # nem dá 409 por o tecido não existir no estoque.
    resp = _vender(ctx["op"], tecido="Linho Importado", tecido_avulso=True,
                   itens=[{"cor": "Cru", "peso": "3,5"}], descontar_estoque=True)
    assert resp.status_code == 201, resp.get_json()
    p = _pedido_db(resp.get_json()["id"])
    assert p["descontar_estoque"] == 0
    assert p["total"] == 35.0
    assert _peso_azul() == 10


def test_tecido_outro_com_mesmo_nome_do_estoque_tambem_nao_baixa(ctx):
    resp = _vender(ctx["op"], tecido="Malha", tecido_avulso=True, descontar_estoque=True)
    assert resp.status_code == 201
    assert _peso_azul() == 10


def test_tecido_do_estoque_mantem_checkbox(ctx):
    assert _vender(ctx["op"], descontar_estoque=True).status_code == 201
    assert _peso_azul() == 8
    resp = _vender(ctx["op"], descontar_estoque=False)
    assert _pedido_db(resp.get_json()["id"])["descontar_estoque"] == 0
    assert _peso_azul() == 8
    # Tecido do estoque sem peso suficiente continua dando 409.
    assert _vender(ctx["op"], itens=[{"cor": "Azul", "peso": "50"}]).status_code == 409
