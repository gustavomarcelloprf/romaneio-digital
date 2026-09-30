# Testes dos dashboards (Fase 1, passo 3): relatórios da loja e do usuário,
# com filtro de período, ranking de tecidos/operadores e isolamento por loja.
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

LOJA = "Loja Dash"
LOJA_B = "Loja Dash B"
SENHA = "s3nh4-forte"

# Período fixo usado nos testes (datas controladas via UPDATE em data_iso).
PERIODO = "?de=2026-01-01&ate=2026-01-31"


def _signup_loja_aprovada(codigo):
    c = app.test_client()
    resp = c.post(
        "/auth/signup",
        json={"nome_loja": codigo, "nome": f"Admin {codigo}", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=%s", (codigo,))
    cur = conn.execute("INSERT INTO clientes (nome, loja) VALUES ('Cliente X', %s) RETURNING id", (codigo,))
    cliente_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    resp = c.post("/auth/login", json={"nome_loja": codigo, "login": "admin", "senha": SENHA})
    assert resp.status_code == 200
    return c, cliente_id


def _post_pedido(c, cliente_id, tecido, peso, preco, data_iso):
    resp = c.post(
        "/pedidos",
        json={
            "cliente_id": cliente_id,
            "preco_unitario": preco,
            "tecido": tecido,
            "itens": [{"cor": "Azul", "peso": peso}],
        },
    )
    assert resp.status_code == 201
    pedido_id = resp.get_json()["id"]
    # A data do pedido é sempre "agora" no POST; fixamos aqui para o teste
    # ser hermético e independente do relógio.
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET data_iso=%s WHERE id=%s", (data_iso, pedido_id))
    conn.commit()
    conn.close()
    return pedido_id


@pytest.fixture()
def ctx():
    """Loja aprovada com admin + operador (taxa 10%), estoque e pedidos.

    Dentro do período 2026-01-01..2026-01-31:
      - operador: Malha  2,0 kg × 10,00 = 20,00 (comissão 2,00)
      - operador: Brim   1,5 kg × 10,00 = 15,00 (comissão 1,50) — no fim do
        último dia (18h), para exercitar o limite "ate + 1 dia".
      - admin:    Malha  1,0 kg × 30,00 = 30,00 (comissão 0,00)
    Fora do período:
      - operador: Malha  5,0 kg × 10,00 = 50,00 em 2025-12-20.
    Estoque: "Malha" (vendeu) e "Tecido Parado" (encalhado).
    """
    admin, cliente_id = _signup_loja_aprovada(LOJA)

    resp = admin.post(
        "/usuarios",
        json={"nome": "Operador Um", "login": "operador", "taxa_comissao": "10"},
    )
    assert resp.status_code == 201
    operador_id = resp.get_json()["id"]
    aceite = app.test_client().post(resp.get_json()["convite_path"], json={"senha": SENHA})
    assert aceite.status_code == 200

    op = app.test_client()
    resp = op.post("/auth/login", json={"nome_loja": LOJA, "login": "operador", "senha": SENHA})
    assert resp.status_code == 200

    for nome in ("Malha", "Tecido Parado"):
        assert admin.post("/api/estoque/tecidos", json={"nome_tecido": nome}).status_code == 201

    _post_pedido(op, cliente_id, "Malha", "2,0", "10,00", "2026-01-10 10:00")
    _post_pedido(op, cliente_id, "Brim", "1,5", "10,00", "2026-01-31 18:00")
    _post_pedido(admin, cliente_id, "Malha", "1,0", "30,00", "2026-01-15 12:00")
    fora_id = _post_pedido(op, cliente_id, "Malha", "5,0", "10,00", "2025-12-20 09:00")

    conn = database.get_conn()
    admin_id = conn.execute(
        "SELECT id FROM usuarios WHERE loja=%s AND login='admin'", (LOJA,)
    ).fetchone()["id"]
    conn.close()

    yield {
        "admin": admin,
        "op": op,
        "cliente_id": cliente_id,
        "admin_id": admin_id,
        "operador_id": operador_id,
        "fora_id": fora_id,
    }


# ---------------------------------------------------------------------------
# /api/relatorio/loja (admin)
# ---------------------------------------------------------------------------
def test_relatorio_loja_totais_e_ticket(ctx):
    resp = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}")
    assert resp.status_code == 200
    rel = resp.get_json()

    # 20 + 15 + 30 = 65; o pedido de 50,00 de 2025-12-20 fica fora.
    assert rel["faturamento_total"] == 65.0
    assert rel["num_pedidos"] == 3
    assert rel["ticket_medio"] == round(65.0 / 3, 2)
    # Comissão vista como despesa do dono: 2,00 + 1,50 do operador
    # (a venda do admin não gera comissão nenhuma).
    assert rel["comissoes_a_pagar"] == 3.5


def test_relatorio_loja_por_operador_ordenado(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    ops = rel["por_operador"]
    assert len(ops) == 2
    # Operador (35,00) na frente do admin (30,00): ordenação por faturamento desc.
    assert ops[0]["usuario_id"] == ctx["operador_id"]
    assert ops[0]["nome"] == "Operador Um"
    assert ops[0]["num_pedidos"] == 2
    assert ops[0]["faturamento"] == 35.0
    assert ops[0]["comissao"] == 3.5
    assert ops[1]["usuario_id"] == ctx["admin_id"]
    assert ops[1]["faturamento"] == 30.0
    assert ops[1]["comissao"] == 0.0


def test_relatorio_loja_ranking_de_tecidos(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()

    mais = rel["tecidos_mais_vendidos"]
    assert mais[0]["tecido"] == "Malha"
    assert mais[0]["faturamento"] == 50.0  # 20 + 30, sem o pedido fora do período
    assert mais[0]["peso_total"] == 3.0    # 2,0 + 1,0 kg

    menos = rel["tecidos_menos_vendidos"]
    assert menos[0]["tecido"] == "Brim"
    assert menos[0]["faturamento"] == 15.0
    assert menos[0]["peso_total"] == 1.5


def test_relatorio_loja_tecidos_encalhados(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert "Tecido Parado" in rel["tecidos_encalhados"]
    assert "Malha" not in rel["tecidos_encalhados"]


def test_relatorio_loja_exclui_pedido_fora_do_periodo(ctx):
    # Num período que cobre só dezembro/2025, apenas o pedido "fora" aparece.
    rel = ctx["admin"].get("/api/relatorio/loja?de=2025-12-01&ate=2025-12-31").get_json()
    assert rel["num_pedidos"] == 1
    assert rel["faturamento_total"] == 50.0


def test_operador_recebe_403_no_relatorio_da_loja(ctx):
    assert ctx["op"].get(f"/api/relatorio/loja{PERIODO}").status_code == 403


# ---------------------------------------------------------------------------
# /api/relatorio/meu (qualquer papel)
# ---------------------------------------------------------------------------
def test_relatorio_meu_do_operador(ctx):
    resp = ctx["op"].get(f"/api/relatorio/meu{PERIODO}")
    assert resp.status_code == 200
    rel = resp.get_json()

    # Só os pedidos do próprio operador (o do admin, de 30,00, fica de fora).
    assert rel["num_pedidos"] == 2
    assert rel["faturamento"] == 35.0
    assert rel["comissao"] == 3.5

    ultimos = rel["ultimos_pedidos"]
    assert len(ultimos) == 2
    assert ultimos[0]["id"] > ultimos[1]["id"]  # mais recentes primeiro
    assert all(p["cliente_nome"] == "Cliente X" for p in ultimos)
    assert {p["total"] for p in ultimos} == {20.0, 15.0}
    assert all(p["id"] != ctx["fora_id"] for p in ultimos)


def test_relatorio_meu_do_admin_ve_so_o_proprio(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/meu{PERIODO}").get_json()
    assert rel["num_pedidos"] == 1
    assert rel["faturamento"] == 30.0
    # O admin é o dono e não recebe comissão: o front esconde o card.
    assert rel["comissao"] == 0.0
    assert rel["mostra_comissao"] is False


def test_relatorio_meu_do_operador_mostra_comissao(ctx):
    rel = ctx["op"].get(f"/api/relatorio/meu{PERIODO}").get_json()
    assert rel["mostra_comissao"] is True


# ---------------------------------------------------------------------------
# Isolamento entre lojas
# ---------------------------------------------------------------------------
def test_relatorio_nao_vaza_pedidos_de_outra_loja(ctx):
    admin_b, cliente_b = _signup_loja_aprovada(LOJA_B)
    _post_pedido(admin_b, cliente_b, "Malha", "9,0", "100,00", "2026-01-05 10:00")

    rel_a = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel_a["faturamento_total"] == 65.0  # inalterado pela venda da loja B
    assert rel_a["num_pedidos"] == 3

    rel_b = admin_b.get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel_b["num_pedidos"] == 1
    assert rel_b["faturamento_total"] == 900.0


# ---------------------------------------------------------------------------
# /api/relatorio/loja: comparação com o período anterior
# ---------------------------------------------------------------------------
def test_relatorio_loja_periodo_anterior_mesma_duracao(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    ant = rel["periodo_anterior"]
    # Janeiro tem 31 dias: o anterior são os 31 dias que terminam na véspera.
    assert ant["de"] == "2025-12-01"
    assert ant["ate"] == "2025-12-31"
    # Só o pedido de 2025-12-20 (operador, 50,00 × 10% = 5,00 de comissão).
    assert ant["faturamento_total"] == 50.0
    assert ant["num_pedidos"] == 1
    assert ant["ticket_medio"] == 50.0
    assert ant["comissoes_a_pagar"] == 5.0
    assert ant["lucro"] == 45.0
    # E o período atual segue com os números de sempre.
    assert rel["lucro"] == 61.5  # 65,00 − 3,50 de comissões


def test_relatorio_loja_periodo_anterior_de_um_dia(ctx):
    rel = ctx["admin"].get("/api/relatorio/loja?de=2026-01-15&ate=2026-01-15").get_json()
    assert rel["faturamento_total"] == 30.0
    ant = rel["periodo_anterior"]
    assert (ant["de"], ant["ate"]) == ("2026-01-14", "2026-01-14")
    assert ant["faturamento_total"] == 0.0
    assert ant["num_pedidos"] == 0
    assert ant["ticket_medio"] == 0.0


def test_relatorio_loja_periodo_anterior_inclui_despesas(ctx):
    resp = ctx["admin"].post(
        "/api/despesas",
        json={"data": "2025-12-10", "descricao": "Luz", "categoria": "Energia", "valor": "20,00"},
    )
    assert resp.status_code == 201
    ant = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()["periodo_anterior"]
    assert ant["despesas_total"] == 20.0
    assert ant["saidas_total"] == 25.0
    assert ant["lucro"] == 25.0  # 50 − 20 de despesa − 5 de comissão


# ---------------------------------------------------------------------------
# /api/relatorio/loja: série diária
# ---------------------------------------------------------------------------
def test_relatorio_loja_serie_diaria_cobre_todos_os_dias(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    serie = rel["serie_diaria"]
    assert len(serie) == 31
    assert serie[0]["dia"] == "2026-01-01"
    assert serie[-1]["dia"] == "2026-01-31"
    por_dia = {p["dia"]: p["total"] for p in serie}
    assert por_dia["2026-01-10"] == 20.0
    assert por_dia["2026-01-15"] == 30.0
    assert por_dia["2026-01-31"] == 15.0  # venda às 18h do último dia
    assert por_dia["2026-01-02"] == 0.0   # dia sem venda também vem, zerado
    assert sum(por_dia.values()) == rel["faturamento_total"]


def test_relatorio_loja_serie_de_periodo_longo_comeca_na_primeira_venda(ctx):
    # O atalho "Tudo" manda de=1970-01-01: a série não traz décadas de zeros.
    rel = ctx["admin"].get("/api/relatorio/loja?de=1970-01-01&ate=2026-01-31").get_json()
    serie = rel["serie_diaria"]
    assert serie[0]["dia"] == "2025-12-20"
    assert serie[-1]["dia"] == "2026-01-31"
    assert sum(p["total"] for p in serie) == rel["faturamento_total"] == 115.0


# ---------------------------------------------------------------------------
# /api/relatorio/loja: filtro por operador
# ---------------------------------------------------------------------------
def test_relatorio_loja_lista_operadores_para_o_filtro(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["usuario_id"] is None
    ids = {o["usuario_id"] for o in rel["operadores"]}
    assert ids == {ctx["admin_id"], ctx["operador_id"]}


def test_relatorio_loja_filtro_por_operador(ctx):
    resp = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}&usuario_id={ctx['operador_id']}")
    assert resp.status_code == 200
    rel = resp.get_json()
    assert rel["usuario_id"] == ctx["operador_id"]
    # Só as vendas do operador: 20 + 15 (a de 30,00 do admin fica de fora).
    assert rel["faturamento_total"] == 35.0
    assert rel["num_pedidos"] == 2
    assert rel["comissoes_a_pagar"] == 3.5
    assert [o["usuario_id"] for o in rel["por_operador"]] == [ctx["operador_id"]]
    assert {t["tecido"]: t["faturamento"] for t in rel["tecidos_mais_vendidos"]} == {
        "Malha": 20.0, "Brim": 15.0,
    }
    por_dia = {p["dia"]: p["total"] for p in rel["serie_diaria"]}
    assert por_dia["2026-01-15"] == 0.0  # venda do admin
    assert sum(por_dia.values()) == 35.0
    # O período anterior respeita o mesmo filtro.
    assert rel["periodo_anterior"]["faturamento_total"] == 50.0
    # A lista de operadores do filtro continua completa.
    assert len(rel["operadores"]) == 2
    # Encalhado é estoque parado da loja: não muda com o filtro.
    assert "Tecido Parado" in rel["tecidos_encalhados"]
    assert "Malha" not in rel["tecidos_encalhados"]


def test_relatorio_loja_filtro_por_operador_ignora_despesas_da_loja(ctx):
    resp = ctx["admin"].post(
        "/api/despesas",
        json={"data": "2026-01-05", "descricao": "Luz", "categoria": "Energia", "valor": "100,00"},
    )
    assert resp.status_code == 201
    geral = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert geral["despesas_total"] == 100.0

    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}&usuario_id={ctx['operador_id']}").get_json()
    # Despesa da loja não é de nenhum operador: o lucro é venda − comissão.
    assert rel["despesas_total"] == 0.0
    assert rel["lucro"] == 31.5
    assert rel["saidas_detalhe"] == [{"categoria": "Comissões (vendas)", "valor": 3.5}]


def test_relatorio_loja_filtro_pelo_admin(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}&usuario_id={ctx['admin_id']}").get_json()
    assert rel["faturamento_total"] == 30.0
    assert rel["comissoes_a_pagar"] == 0.0  # o admin não recebe comissão
    assert rel["periodo_anterior"]["faturamento_total"] == 0.0


def test_relatorio_loja_filtro_por_operador_invalido(ctx):
    resp = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}&usuario_id=abc")
    assert resp.status_code == 400


def test_relatorio_loja_filtro_por_operador_de_outra_loja_nao_vaza(ctx):
    admin_b, cliente_b = _signup_loja_aprovada(LOJA_B)
    _post_pedido(admin_b, cliente_b, "Malha", "9,0", "100,00", "2026-01-05 10:00")
    conn = database.get_conn()
    admin_b_id = conn.execute(
        "SELECT id FROM usuarios WHERE loja=%s AND login='admin'", (LOJA_B,)
    ).fetchone()["id"]
    conn.close()

    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}&usuario_id={admin_b_id}").get_json()
    assert rel["faturamento_total"] == 0.0
    assert rel["num_pedidos"] == 0
    assert rel["por_operador"] == []
    assert all(p["total"] == 0.0 for p in rel["serie_diaria"])
    assert admin_b_id not in {o["usuario_id"] for o in rel["operadores"]}


def test_operador_recebe_403_mesmo_com_filtro(ctx):
    resp = ctx["op"].get(f"/api/relatorio/loja{PERIODO}&usuario_id={ctx['operador_id']}")
    assert resp.status_code == 403
