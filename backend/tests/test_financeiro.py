# Testes do financeiro do admin: lucro real (faturamento − despesas −
# comissões), saídas detalhadas, gastos recorrentes com lançamento mensal,
# despesa avulsa e bloqueio para operador/gerente.
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

LOJA = "Loja Financeiro"
LOJA_B = "Loja Financeiro B"
SENHA = "s3nh4-forte"
PERIODO = "?de=2026-01-01&ate=2026-01-31"


def _login(loja, login):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": loja, "login": login, "senha": SENHA})
    assert resp.status_code == 200
    return c


def _signup_loja_aprovada(codigo):
    c = app.test_client()
    resp = c.post(
        "/auth/signup",
        json={"nome_loja": codigo, "nome": f"Admin {codigo}", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=%s", (codigo,))
    conn.commit()
    conn.close()
    return _login(codigo, "admin")


def _pedido(client, cliente_id, data_iso):
    """Pedido de R$ 100,00 (10 kg × R$ 10,00) datado em data_iso."""
    resp = client.post(
        "/pedidos",
        json={"cliente_id": cliente_id, "preco_unitario": "10,00", "tecido": "Malha",
              "itens": [{"cor": "Azul", "peso": "10"}]},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET data_iso=%s WHERE id=%s", (data_iso, resp.get_json()["id"]))
    conn.commit()
    conn.close()


@pytest.fixture()
def ctx():
    """Loja com admin, operador (10% de comissão) e gerente. No período há
    um pedido do admin e um do operador, R$ 100,00 cada: faturamento 200,00
    e comissões a pagar 10,00 (o admin não recebe comissão)."""
    admin = _signup_loja_aprovada(LOJA)
    conn = database.get_conn()
    # Operador e gerente nascem direto no banco, já com senha, para logar.
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao, ativo) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1)",
            [
                (LOJA, "Operador", "operador", generate_password_hash(SENHA), "operador", 10),
                (LOJA, "Gerente", "gerente", generate_password_hash(SENHA), "gerente", 0),
            ],
        )
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    op = _login(LOJA, "operador")
    _pedido(admin, cliente_id, "2026-01-10 10:00")
    _pedido(op, cliente_id, "2026-01-11 10:00")

    yield {"admin": admin, "op": op, "gerente": _login(LOJA, "gerente")}


def _despesas_no_banco(loja=LOJA):
    conn = database.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT data, descricao, categoria, valor, recorrente_id FROM despesas WHERE loja=%s ORDER BY id",
        (loja,),
    ).fetchall()]
    conn.close()
    return rows


def _nova_despesa(client, **kw):
    body = {"data": "2026-01-05", "descricao": "Luz", "categoria": "Energia", "valor": "150,50"}
    body.update(kw)
    return client.post("/api/despesas", json=body)


def _novo_recorrente(client, nome, categoria):
    resp = client.post("/api/despesas/recorrentes", json={"nome": nome, "categoria": categoria})
    assert resp.status_code == 201
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Dashboard: lucro real e saídas detalhadas
# ---------------------------------------------------------------------------
def test_lucro_subtrai_despesas_e_comissoes(ctx):
    _nova_despesa(ctx["admin"], valor="30", categoria="Energia")
    _nova_despesa(ctx["admin"], valor="20", categoria="Aluguel")
    _nova_despesa(ctx["admin"], data="2025-12-31", valor="999")  # fora do período

    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["faturamento_total"] == 200.0
    assert rel["despesas_total"] == 50.0
    assert rel["comissoes_a_pagar"] == 10.0
    assert rel["saidas_total"] == 60.0
    assert rel["lucro"] == 140.0


def test_saidas_detalhe_traz_categorias_e_linha_de_comissoes(ctx):
    _nova_despesa(ctx["admin"], valor="30", categoria="Energia")
    _nova_despesa(ctx["admin"], valor="5", categoria="Energia")
    _nova_despesa(ctx["admin"], valor="20", categoria="Aluguel")

    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["saidas_detalhe"] == [
        {"categoria": "Energia", "valor": 35.0},
        {"categoria": "Aluguel", "valor": 20.0},
        {"categoria": "Comissões (vendas)", "valor": 10.0},
    ]
    # Comissão fica fora das categorias de despesa.
    assert rel["despesas_por_categoria"] == [
        {"categoria": "Energia", "total": 35.0},
        {"categoria": "Aluguel", "total": 20.0},
    ]
    assert rel["saidas_total"] == sum(s["valor"] for s in rel["saidas_detalhe"])


def test_lucro_negativo_quando_saidas_superam_faturamento(ctx):
    _nova_despesa(ctx["admin"], valor="500")
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["lucro"] == 200.0 - 500.0 - 10.0


def test_gerente_nao_recebe_saidas_nem_lucro(ctx):
    rel = ctx["gerente"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["faturamento_total"] == 200.0
    for chave in ("saidas_total", "saidas_detalhe", "lucro", "comissoes_a_pagar", "despesas_total"):
        assert chave not in rel


# ---------------------------------------------------------------------------
# Despesa avulsa
# ---------------------------------------------------------------------------
def test_despesa_avulsa_cria(ctx):
    resp = _nova_despesa(ctx["admin"], data="20/01/2026", descricao="Conserto", categoria="Manutenção",
                         valor="1.234,56")
    assert resp.status_code == 201
    assert resp.get_json()["valor"] == 1234.56
    assert _despesas_no_banco() == [
        {"data": "2026-01-20", "descricao": "Conserto", "categoria": "Manutenção",
         "valor": 1234.56, "recorrente_id": None},
    ]


@pytest.mark.parametrize("campos", [{"valor": "0"}, {"valor": "-3"}, {"valor": "abc"},
                                    {"data": "31/02/2026"}, {"data": ""}])
def test_despesa_avulsa_rejeita_invalida(ctx, campos):
    assert _nova_despesa(ctx["admin"], **campos).status_code == 400
    assert _despesas_no_banco() == []


# ---------------------------------------------------------------------------
# Gastos recorrentes: CRUD
# ---------------------------------------------------------------------------
def test_crud_recorrentes(ctx):
    admin = ctx["admin"]
    assert admin.post("/api/despesas/recorrentes", json={"nome": "  "}).status_code == 400

    rid = _novo_recorrente(admin, "Aluguel", "Aluguel")
    sid = _novo_recorrente(admin, "Salário Maria", "Salários")
    lista = admin.get("/api/despesas/recorrentes").get_json()
    assert {r["nome"] for r in lista} == {"Aluguel", "Salário Maria"}
    assert all(r["ativo"] for r in lista)

    resp = admin.put(f"/api/despesas/recorrentes/{rid}", json={"nome": "Aluguel loja", "categoria": "Imóvel"})
    assert resp.status_code == 200
    assert resp.get_json() == {"id": rid, "nome": "Aluguel loja", "categoria": "Imóvel", "ativo": True}

    resp = admin.put(f"/api/despesas/recorrentes/{sid}", json={"ativo": False})
    assert resp.get_json()["ativo"] is False
    assert resp.get_json()["nome"] == "Salário Maria"  # campo ausente não muda
    resp = admin.put(f"/api/despesas/recorrentes/{sid}", json={"ativo": True})
    assert resp.get_json()["ativo"] is True

    assert admin.put(f"/api/despesas/recorrentes/{rid}", json={"nome": ""}).status_code == 400
    assert admin.put("/api/despesas/recorrentes/9999", json={"nome": "X"}).status_code == 404


def test_recorrentes_isolados_entre_lojas(ctx):
    rid = _novo_recorrente(ctx["admin"], "Aluguel", "Aluguel")
    admin_b = _signup_loja_aprovada(LOJA_B)
    assert admin_b.get("/api/despesas/recorrentes").get_json() == []
    assert admin_b.put(f"/api/despesas/recorrentes/{rid}", json={"nome": "X"}).status_code == 404
    resp = admin_b.post("/api/despesas/lancar-mes",
                        json={"data": "2026-01-05", "itens": [{"recorrente_id": rid, "valor": 10}]})
    assert resp.status_code == 400
    assert _despesas_no_banco(LOJA_B) == []


# ---------------------------------------------------------------------------
# Sugestão e lançamento do mês
# ---------------------------------------------------------------------------
def test_sugestao_traz_valor_do_ultimo_lancamento(ctx):
    admin = ctx["admin"]
    luz = _novo_recorrente(admin, "Conta de luz", "Energia")
    nunca = _novo_recorrente(admin, "Internet", "Serviços")
    inativo = _novo_recorrente(admin, "Antigo", "Geral")
    admin.put(f"/api/despesas/recorrentes/{inativo}", json={"ativo": False})

    for data, valor in (("2026-01-05", 180), ("2026-02-05", 210.4), ("2025-12-05", 999)):
        resp = admin.post("/api/despesas/lancar-mes",
                          json={"data": data, "itens": [{"recorrente_id": luz, "valor": valor}]})
        assert resp.status_code == 201
    # Uma despesa avulsa da mesma categoria não conta como lançamento do modelo.
    _nova_despesa(admin, data="2026-03-01", categoria="Energia", descricao="Conta de luz", valor="1")

    sug = {s["id"]: s for s in admin.get("/api/despesas/recorrentes/sugestao").get_json()}
    assert set(sug) == {luz, nunca}  # inativo não aparece
    assert sug[luz]["valor_sugerido"] == 210.4  # o lançamento mais recente (fev)
    assert sug[luz]["nome"] == "Conta de luz" and sug[luz]["categoria"] == "Energia"
    assert sug[nunca]["valor_sugerido"] == 0


def test_lancar_mes_cria_despesas_herdando_do_modelo(ctx):
    admin = ctx["admin"]
    aluguel = _novo_recorrente(admin, "Aluguel", "Aluguel")
    maria = _novo_recorrente(admin, "Salário Maria", "Salários")
    resp = admin.post("/api/despesas/lancar-mes", json={
        "data": "2026-01-15",
        "itens": [{"recorrente_id": aluguel, "valor": "1.500,00"},
                  {"recorrente_id": maria, "valor": 2000}],
    })
    assert resp.status_code == 201
    assert resp.get_json() == {"lancadas": 2, "erros": [], "total": 3500.0}
    assert _despesas_no_banco() == [
        {"data": "2026-01-15", "descricao": "Aluguel", "categoria": "Aluguel",
         "valor": 1500.0, "recorrente_id": aluguel},
        {"data": "2026-01-15", "descricao": "Salário Maria", "categoria": "Salários",
         "valor": 2000.0, "recorrente_id": maria},
    ]
    rel = admin.get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["despesas_total"] == 3500.0
    assert rel["lucro"] == 200.0 - 3500.0 - 10.0


def test_lancar_mes_recusa_linhas_invalidas(ctx):
    admin = ctx["admin"]
    ok = _novo_recorrente(admin, "Aluguel", "Aluguel")
    zero = _novo_recorrente(admin, "Luz", "Energia")
    neg = _novo_recorrente(admin, "Água", "Energia")
    ruim = _novo_recorrente(admin, "Internet", "Serviços")
    resp = admin.post("/api/despesas/lancar-mes", json={
        "data": "2026-01-15",
        "itens": [{"recorrente_id": ok, "valor": 100},
                  {"recorrente_id": zero, "valor": 0},
                  {"recorrente_id": neg, "valor": -5},
                  {"recorrente_id": ruim, "valor": "abc"},
                  {"recorrente_id": 9999, "valor": 10},
                  {"recorrente_id": ok, "valor": 100}],
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["lancadas"] == 1 and body["total"] == 100.0
    assert [e["linha"] for e in body["erros"]] == [2, 3, 4, 5, 6]
    assert len(_despesas_no_banco()) == 1


def test_lancar_mes_sem_itens_validos_nao_grava(ctx):
    admin = ctx["admin"]
    rid = _novo_recorrente(admin, "Aluguel", "Aluguel")
    assert admin.post("/api/despesas/lancar-mes",
                      json={"data": "2026-01-15", "itens": [{"recorrente_id": rid, "valor": 0}]}
                      ).status_code == 400
    assert admin.post("/api/despesas/lancar-mes", json={"data": "2026-01-15", "itens": []}).status_code == 400
    assert admin.post("/api/despesas/lancar-mes",
                      json={"data": "xx", "itens": [{"recorrente_id": rid, "valor": 10}]}
                      ).status_code == 400
    assert _despesas_no_banco() == []


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["op", "gerente"])
def test_operador_e_gerente_recebem_403_nas_rotas_de_despesa(ctx, papel):
    rid = _novo_recorrente(ctx["admin"], "Aluguel", "Aluguel")
    c = ctx[papel]
    assert _nova_despesa(c).status_code == 403
    assert c.get("/api/despesas/recorrentes").status_code == 403
    assert c.post("/api/despesas/recorrentes", json={"nome": "X"}).status_code == 403
    assert c.put(f"/api/despesas/recorrentes/{rid}", json={"ativo": False}).status_code == 403
    assert c.get("/api/despesas/recorrentes/sugestao").status_code == 403
    assert c.post("/api/despesas/lancar-mes",
                  json={"data": "2026-01-15", "itens": [{"recorrente_id": rid, "valor": 10}]}
                  ).status_code == 403
    assert _despesas_no_banco() == []
    assert len(ctx["admin"].get("/api/despesas/recorrentes").get_json()) == 1


def test_admin_acessa_rotas_de_despesa(ctx):
    admin = ctx["admin"]
    rid = _novo_recorrente(admin, "Aluguel", "Aluguel")
    assert _nova_despesa(admin).status_code == 201
    assert admin.get("/api/despesas/recorrentes").status_code == 200
    assert admin.put(f"/api/despesas/recorrentes/{rid}", json={"ativo": True}).status_code == 200
    assert admin.get("/api/despesas/recorrentes/sugestao").status_code == 200
    assert admin.post("/api/despesas/lancar-mes",
                      json={"data": "2026-01-15", "itens": [{"recorrente_id": rid, "valor": 10}]}
                      ).status_code == 201
