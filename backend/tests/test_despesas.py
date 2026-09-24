# Testes das despesas: importação de planilha (csv/xlsx), listagem por
# período, lucro no dashboard do admin e bloqueio para operador/gerente.
import io
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
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

LOJA = "Loja Despesas"
LOJA_B = "Loja Despesas B"
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


def _csv(texto, nome="despesas.csv"):
    return {"arquivo": (io.BytesIO(texto.encode("utf-8")), nome)}


def _xlsx(linhas, nome="despesas.xlsx"):
    wb = Workbook()
    ws = wb.active
    for linha in linhas:
        ws.append(linha)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return {"arquivo": (buf, nome)}


def _importar(client, arquivo):
    return client.post("/api/despesas/importar", data=arquivo, content_type="multipart/form-data")


@pytest.fixture()
def ctx():
    """Loja aprovada com admin, operador e gerente, e um pedido de R$ 100,00
    em 2026-01-10 (faturamento do período = 100,00)."""
    admin = _signup_loja_aprovada(LOJA)
    resp = admin.post(
        "/usuarios",
        json={"nome": "Operador", "login": "operador", "taxa_comissao": "0"},
    )
    assert resp.status_code == 201
    aceite = app.test_client().post(resp.get_json()["convite_path"], json={"senha": SENHA})
    assert aceite.status_code == 200

    conn = database.get_conn()
    # Não há UI para criar gerente: o papel nasce direto no banco.
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel, taxa_comissao) "
        "VALUES (%s, 'Gerente', 'gerente', %s, 'gerente', 0)",
        (LOJA, generate_password_hash(SENHA)),
    )
    cliente_id = conn.execute(
        "INSERT INTO clientes (nome, loja) VALUES ('Cliente', %s) RETURNING id", (LOJA,)
    ).fetchone()["id"]
    conn.commit()
    conn.close()

    resp = admin.post(
        "/pedidos",
        json={"cliente_id": cliente_id, "preco_unitario": "10,00", "tecido": "Malha",
              "itens": [{"cor": "Azul", "peso": "10"}]},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE pedidos SET data_iso='2026-01-10 10:00' WHERE id=%s", (resp.get_json()["id"],))
    conn.commit()
    conn.close()

    yield {
        "admin": admin,
        "op": _login(LOJA, "operador"),
        "gerente": _login(LOJA, "gerente"),
    }


def _despesas_no_banco(loja=LOJA):
    conn = database.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT data, descricao, categoria, valor FROM despesas WHERE loja=%s ORDER BY id", (loja,)
    ).fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Importação
# ---------------------------------------------------------------------------
def test_importar_csv_cria_despesas(ctx):
    csv_txt = (
        "data;descricao;categoria;valor\n"
        "05/01/2026;Conta de luz;Energia;150,50\n"
        "2026-01-20;Aluguel;Aluguel;1.200,00\n"
    )
    resp = _importar(ctx["admin"], _csv(csv_txt))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"importadas": 2, "erros": [], "total": 1350.5}
    assert _despesas_no_banco() == [
        {"data": "2026-01-05", "descricao": "Conta de luz", "categoria": "Energia", "valor": 150.5},
        {"data": "2026-01-20", "descricao": "Aluguel", "categoria": "Aluguel", "valor": 1200.0},
    ]


def test_importar_xlsx_cria_despesas(ctx):
    arquivo = _xlsx([
        ["Data", "Descrição", "Categoria", "Valor"],
        [datetime(2026, 1, 8), "Frete", "Logística", 80],
        ["15/01/2026", "Internet", "Serviços", "99,90"],
    ])
    resp = _importar(ctx["admin"], arquivo)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["importadas"] == 2
    assert body["erros"] == []
    assert body["total"] == 179.9
    assert _despesas_no_banco() == [
        {"data": "2026-01-08", "descricao": "Frete", "categoria": "Logística", "valor": 80.0},
        {"data": "2026-01-15", "descricao": "Internet", "categoria": "Serviços", "valor": 99.9},
    ]


def test_importar_valida_linha_a_linha(ctx):
    csv_txt = (
        "data,descricao,categoria,valor\n"
        "2026-01-05,Ok,Geral,10.50\n"
        "31/02/2026,Data impossível,Geral,10\n"
        "2026-01-06,Valor ruim,Geral,abc\n"
        ",,,\n"
        "2026-01-07,Negativo,Geral,-5\n"
    )
    body = _importar(ctx["admin"], _csv(csv_txt)).get_json()
    assert body["importadas"] == 1
    assert body["total"] == 10.5
    assert [e["linha"] for e in body["erros"]] == [3, 4, 6]
    assert "data" in body["erros"][0]["erro"]
    assert "valor" in body["erros"][1]["erro"]
    assert len(_despesas_no_banco()) == 1


def test_importar_rejeita_formato_e_cabecalho(ctx):
    resp = _importar(ctx["admin"], {"arquivo": (io.BytesIO(b"x"), "despesas.txt")})
    assert resp.status_code == 400
    resp = _importar(ctx["admin"], _csv("descricao,categoria\nLuz,Energia\n"))
    assert resp.status_code == 400
    assert "data" in resp.get_json()["error"]
    assert _despesas_no_banco() == []


# ---------------------------------------------------------------------------
# Listagem, remoção e isolamento
# ---------------------------------------------------------------------------
def test_listar_filtra_por_periodo(ctx):
    _importar(ctx["admin"], _csv(
        "data,descricao,categoria,valor\n"
        "2026-01-31,Dentro,Geral,10\n"
        "2025-12-31,Fora,Geral,99\n"
    ))
    body = ctx["admin"].get(f"/api/despesas{PERIODO}").get_json()
    assert body["periodo"] == {"de": "2026-01-01", "ate": "2026-01-31"}
    assert [d["descricao"] for d in body["despesas"]] == ["Dentro"]
    assert body["total"] == 10.0


def test_remover_despesa_e_isolamento_entre_lojas(ctx):
    _importar(ctx["admin"], _csv("data,descricao,categoria,valor\n2026-01-05,Luz,Energia,10\n"))
    despesa_id = ctx["admin"].get(f"/api/despesas{PERIODO}").get_json()["despesas"][0]["id"]

    admin_b = _signup_loja_aprovada(LOJA_B)
    assert admin_b.get(f"/api/despesas{PERIODO}").get_json()["despesas"] == []
    assert admin_b.delete(f"/api/despesas/{despesa_id}").status_code == 404

    assert ctx["admin"].delete(f"/api/despesas/{despesa_id}").status_code == 200
    assert _despesas_no_banco() == []


# ---------------------------------------------------------------------------
# Dashboard: lucro = faturamento − despesas (somente admin)
# ---------------------------------------------------------------------------
def test_relatorio_loja_do_admin_traz_despesas_e_lucro(ctx):
    _importar(ctx["admin"], _csv(
        "data,descricao,categoria,valor\n"
        "2026-01-05,Luz,Energia,15\n"
        "2026-01-06,Água,Energia,5\n"
        "2026-01-07,Frete,Logística,30\n"
        "2025-12-15,Fora do período,Energia,500\n"
    ))
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["faturamento_total"] == 100.0
    assert rel["despesas_total"] == 50.0
    assert rel["lucro"] == 50.0
    assert rel["despesas_por_categoria"] == [
        {"categoria": "Logística", "total": 30.0},
        {"categoria": "Energia", "total": 20.0},
    ]


def test_relatorio_loja_sem_despesas_lucro_igual_faturamento(ctx):
    rel = ctx["admin"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["despesas_total"] == 0
    assert rel["despesas_por_categoria"] == []
    assert rel["lucro"] == rel["faturamento_total"] == 100.0


# ---------------------------------------------------------------------------
# Permissões: operador e gerente ficam de fora
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["op", "gerente"])
def test_operador_e_gerente_recebem_403_nas_rotas_de_despesa(ctx, papel):
    c = ctx[papel]
    arquivo = _csv("data,descricao,categoria,valor\n2026-01-05,Luz,Energia,10\n")
    assert _importar(c, arquivo).status_code == 403
    assert c.get(f"/api/despesas{PERIODO}").status_code == 403
    assert c.delete("/api/despesas/1").status_code == 403
    assert _despesas_no_banco() == []


def test_gerente_nao_recebe_lucro_nem_despesas_no_relatorio(ctx):
    _importar(ctx["admin"], _csv("data,descricao,categoria,valor\n2026-01-05,Luz,Energia,10\n"))
    rel = ctx["gerente"].get(f"/api/relatorio/loja{PERIODO}").get_json()
    assert rel["faturamento_total"] == 100.0
    for chave in ("despesas_total", "despesas_por_categoria", "lucro"):
        assert chave not in rel


def test_operador_nao_recebe_lucro_nem_despesas_no_relatorio(ctx):
    # O operador nem entra no relatório da loja; o dele não traz esses números.
    assert ctx["op"].get(f"/api/relatorio/loja{PERIODO}").status_code == 403
    rel = ctx["op"].get(f"/api/relatorio/meu{PERIODO}").get_json()
    for chave in ("despesas_total", "despesas_por_categoria", "lucro"):
        assert chave not in rel
