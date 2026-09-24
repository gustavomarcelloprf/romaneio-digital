# Testes do convite de primeiro acesso: o admin cria o operador sem senha,
# o operador define a própria senha pelo link /convite/<token> e só então
# consegue logar. Inclui a capacidade semântica despesas_gerir.
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

# app.py exige SECRET_KEY e ADMIN_TOKEN no import (boot fail-fast).
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

_TMP_DB = Path(__file__).parent / f"_test_{uuid.uuid4().hex}.db"
database.DB_PATH = str(_TMP_DB)

import app as app_module  # noqa: E402
from app import app  # noqa: E402

app_module.limiter.enabled = False

LOJA = "Loja Convite"
SENHA = "s3nh4-forte"
SENHA_OP = "senha-do-operador"


def _login(login, senha):
    c = app.test_client()
    resp = c.post("/auth/login", json={"nome_loja": LOJA, "login": login, "senha": senha})
    return c, resp


def _usuario_db(login):
    conn = database.get_conn()
    row = conn.execute(
        "SELECT senha_hash, convite_token, convite_expira, ativo FROM usuarios WHERE loja=? AND login=?",
        (LOJA, login),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture()
def admin():
    """Loja aprovada com o admin logado."""
    database.DB_PATH = str(_TMP_DB)
    _TMP_DB.unlink(missing_ok=True)
    database.init_db()

    c = app.test_client()
    resp = c.post(
        "/auth/signup",
        json={"nome_loja": LOJA, "nome": "Dona", "login": "admin", "senha": SENHA},
    )
    assert resp.status_code == 201
    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=?", (LOJA,))
    conn.commit()
    conn.close()
    c, resp = _login("admin", SENHA)
    assert resp.status_code == 200
    yield c
    _TMP_DB.unlink(missing_ok=True)


def _criar_operador(admin, login="operador"):
    resp = admin.post("/usuarios", json={"nome": "Operador", "login": login, "taxa_comissao": "5"})
    assert resp.status_code == 201
    return resp.get_json()


# ---------------------------------------------------------------------------
# Criação: sem senha, com convite
# ---------------------------------------------------------------------------
def test_criar_operador_gera_convite_sem_senha(admin):
    corpo = _criar_operador(admin)
    token = corpo["convite_token"]
    assert len(token) >= 32
    assert corpo["convite_path"] == f"/convite/{token}"
    assert corpo["convite_url"].endswith(corpo["convite_path"])
    assert corpo["papel"] == "operador"

    u = _usuario_db("operador")
    assert u["senha_hash"] is None
    assert u["convite_token"] == token
    assert u["convite_expira"] > app_module._agora_utc_str()
    assert u["ativo"] == 1

    lista = admin.get("/usuarios").get_json()
    op = next(x for x in lista if x["login"] == "operador")
    assert op["convite_pendente"] == 1
    adm = next(x for x in lista if x["login"] == "admin")
    assert adm["convite_pendente"] == 0


def test_criar_operador_nao_exige_senha_mas_exige_nome_e_login(admin):
    assert admin.post("/usuarios", json={"nome": "Sem login"}).status_code == 400
    assert admin.post("/usuarios", json={"login": "sem-nome"}).status_code == 400


def test_senha_enviada_na_criacao_e_ignorada(admin):
    # Mesmo que alguém mande senha, quem define é o operador pelo convite.
    resp = admin.post("/usuarios", json={"nome": "Op", "login": "operador", "senha": SENHA_OP})
    assert resp.status_code == 201
    assert _usuario_db("operador")["senha_hash"] is None
    _, r = _login("operador", SENHA_OP)
    assert r.status_code == 401


def test_login_duplicado_continua_409(admin):
    _criar_operador(admin)
    resp = admin.post("/usuarios", json={"nome": "Outro", "login": "operador"})
    assert resp.status_code == 409


def test_operador_sem_senha_nao_loga(admin):
    _criar_operador(admin)
    for tentativa in ("", "qualquer", SENHA):
        _, resp = _login("operador", tentativa)
        # senha vazia cai no 400 de campo obrigatório; o resto é 401.
        assert resp.status_code in (400, 401)
    _, resp = _login("operador", "qualquer")
    assert resp.get_json()["error"] == "Credenciais inválidas."


# ---------------------------------------------------------------------------
# Aceite do convite
# ---------------------------------------------------------------------------
def test_get_convite_valido_serve_pagina(admin):
    corpo = _criar_operador(admin)
    resp = app.test_client().get(corpo["convite_path"])
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Definir senha" in html
    assert "formConvite" in html
    assert LOJA in html


def test_post_convite_define_senha_e_habilita_login(admin):
    corpo = _criar_operador(admin)
    anon = app.test_client()
    resp = anon.post(corpo["convite_path"], json={"senha": SENHA_OP})
    assert resp.status_code == 200
    assert resp.get_json()["login"] == "operador"
    assert resp.get_json()["loja"] == LOJA

    u = _usuario_db("operador")
    assert u["senha_hash"] and u["senha_hash"] != SENHA_OP
    assert u["convite_token"] is None
    assert u["convite_expira"] is None

    c, resp = _login("operador", SENHA_OP)
    assert resp.status_code == 200
    assert resp.get_json()["papel"] == "operador"
    assert c.get("/me").status_code == 200

    lista = admin.get("/usuarios").get_json()
    assert next(x for x in lista if x["login"] == "operador")["convite_pendente"] == 0


def test_convite_e_de_uso_unico(admin):
    corpo = _criar_operador(admin)
    anon = app.test_client()
    assert anon.post(corpo["convite_path"], json={"senha": SENHA_OP}).status_code == 200
    # Reusar o link não troca a senha de quem já aceitou.
    assert anon.post(corpo["convite_path"], json={"senha": "outra-senha"}).status_code == 404
    assert anon.get(corpo["convite_path"]).status_code == 404
    _, resp = _login("operador", SENHA_OP)
    assert resp.status_code == 200


def test_convite_senha_curta_recusada_e_convite_segue_valido(admin):
    corpo = _criar_operador(admin)
    anon = app.test_client()
    assert anon.post(corpo["convite_path"], json={"senha": "123"}).status_code == 400
    assert anon.post(corpo["convite_path"], json={}).status_code == 400
    assert _usuario_db("operador")["convite_token"] == corpo["convite_token"]
    assert anon.post(corpo["convite_path"], json={"senha": SENHA_OP}).status_code == 200


@pytest.mark.parametrize("token", ["token-que-nao-existe", "x" * 43])
def test_token_invalido_recusado(admin, token):
    _criar_operador(admin)
    anon = app.test_client()
    resp = anon.get(f"/convite/{token}")
    assert resp.status_code == 404
    assert "inválido ou expirado" in resp.get_data(as_text=True)
    assert "formConvite" not in resp.get_data(as_text=True)
    assert anon.post(f"/convite/{token}", json={"senha": SENHA_OP}).status_code == 404
    assert _usuario_db("operador")["senha_hash"] is None


def test_token_expirado_recusado(admin):
    corpo = _criar_operador(admin)
    conn = database.get_conn()
    conn.execute(
        "UPDATE usuarios SET convite_expira = '2000-01-01 00:00:00' WHERE loja=? AND login='operador'",
        (LOJA,),
    )
    conn.commit()
    conn.close()

    anon = app.test_client()
    assert anon.get(corpo["convite_path"]).status_code == 404
    assert anon.post(corpo["convite_path"], json={"senha": SENHA_OP}).status_code == 404
    assert _usuario_db("operador")["senha_hash"] is None
    _, resp = _login("operador", SENHA_OP)
    assert resp.status_code == 401


def test_convite_de_operador_desativado_recusado(admin):
    corpo = _criar_operador(admin)
    assert admin.put(f"/usuarios/{corpo['id']}", json={"ativo": 0}).status_code == 200
    anon = app.test_client()
    assert anon.get(corpo["convite_path"]).status_code == 404
    assert anon.post(corpo["convite_path"], json={"senha": SENHA_OP}).status_code == 404


def test_admin_definir_senha_invalida_convite(admin):
    corpo = _criar_operador(admin)
    assert admin.put(f"/usuarios/{corpo['id']}", json={"senha": SENHA_OP}).status_code == 200
    assert _usuario_db("operador")["convite_token"] is None
    assert app.test_client().post(corpo["convite_path"], json={"senha": "outra-senha"}).status_code == 404
    _, resp = _login("operador", SENHA_OP)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# despesas_gerir: capacidade própria, continua só-admin
# ---------------------------------------------------------------------------
def test_despesas_gerir_e_so_do_admin():
    assert app_module.PERMISSOES["despesas_gerir"] == ("admin",)
    assert app_module.PERM_DESPESAS == "despesas_gerir"
    assert app_module.pode("despesas_gerir", "admin")
    assert not app_module.pode("despesas_gerir", "gerente")
    assert not app_module.pode("despesas_gerir", "operador")


def test_me_expoe_despesas_gerir(admin):
    assert admin.get("/me").get_json()["permissoes"]["despesas_gerir"] is True
    corpo = _criar_operador(admin)
    app.test_client().post(corpo["convite_path"], json={"senha": SENHA_OP})
    op, _ = _login("operador", SENHA_OP)
    assert op.get("/me").get_json()["permissoes"]["despesas_gerir"] is False


def test_rotas_de_despesa_consultam_despesas_gerir(admin, monkeypatch):
    corpo = _criar_operador(admin)
    app.test_client().post(corpo["convite_path"], json={"senha": SENHA_OP})
    op, _ = _login("operador", SENHA_OP)

    assert admin.get("/api/despesas").status_code == 200
    assert op.get("/api/despesas").status_code == 403
    assert op.delete("/api/despesas/1").status_code == 403
    # Mexer só em despesas_gerir muda o acesso: as rotas dependem dela,
    # não de relatorio_comissoes.
    monkeypatch.setitem(app_module.PERMISSOES, "despesas_gerir", ("operador", "admin"))
    assert op.get("/api/despesas").status_code == 200
    monkeypatch.setitem(app_module.PERMISSOES, "despesas_gerir", ())
    assert admin.get("/api/despesas").status_code == 403
