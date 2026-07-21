# Testes do modelo multi-usuário (Fase 1): signup cria loja pendente +
# usuário admin, login por usuário, revalidação de sessão e papéis.
import os
import sys
import uuid
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import database

# app.py exige SECRET_KEY e ADMIN_TOKEN no import (boot fail-fast).
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")

# Redireciona o banco para um arquivo temporário ANTES de importar o app
# (app.py chama init_db() no import).
_TMP_DB = Path(__file__).parent / f"_test_{uuid.uuid4().hex}.db"
database.DB_PATH = str(_TMP_DB)

import app as app_module  # noqa: E402
from app import app  # noqa: E402

# O rate limit do /auth/login (5/min) atrapalharia a suíte, que loga mais
# de 5 vezes; desligado só nos testes.
app_module.limiter.enabled = False

LOJA = "Loja Teste"
SENHA = "s3nh4-forte"


@pytest.fixture()
def banco():
    """Banco limpo por teste (reafirma DB_PATH: outro módulo pode tê-lo trocado)."""
    database.DB_PATH = str(_TMP_DB)
    _TMP_DB.unlink(missing_ok=True)
    database.init_db()
    yield
    _TMP_DB.unlink(missing_ok=True)


def _signup(c, nome_loja=LOJA, nome="Dona da Loja", login="dona", senha=SENHA):
    return c.post(
        "/auth/signup",
        json={"nome_loja": nome_loja, "nome": nome, "login": login, "senha": senha},
    )


def _login(c, nome_loja=LOJA, login="dona", senha=SENHA):
    return c.post(
        "/auth/login", json={"nome_loja": nome_loja, "login": login, "senha": senha}
    )


def _aprovar_loja(codigo=LOJA):
    conn = database.get_conn()
    conn.execute("UPDATE lojas SET status='aprovado' WHERE codigo=?", (codigo,))
    conn.commit()
    conn.close()


def test_signup_cria_loja_pendente_e_usuario_admin(banco):
    c = app.test_client()
    resp = _signup(c)
    assert resp.status_code == 201

    conn = database.get_conn()
    loja = conn.execute("SELECT status FROM lojas WHERE codigo=?", (LOJA,)).fetchone()
    usuario = conn.execute(
        "SELECT nome, login, papel, taxa_comissao, ativo FROM usuarios WHERE loja=?",
        (LOJA,),
    ).fetchone()
    conn.close()

    assert loja["status"] == "pendente"
    assert usuario["login"] == "dona"
    assert usuario["papel"] == "admin"
    assert usuario["taxa_comissao"] == 0
    assert usuario["ativo"] == 1


def test_signup_loja_duplicada_retorna_409(banco):
    c = app.test_client()
    assert _signup(c).status_code == 201
    resp = _signup(c, login="outra-pessoa")
    assert resp.status_code == 409
    assert "loja" in resp.get_json()["error"].lower()


def test_login_falha_enquanto_loja_pendente(banco):
    c = app.test_client()
    _signup(c)
    resp = _login(c)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Credenciais inválidas."


def test_login_apos_aprovacao_com_sessao_admin(banco):
    c = app.test_client()
    _signup(c)
    _aprovar_loja()
    resp = _login(c)
    assert resp.status_code == 200
    with c.session_transaction() as s:
        assert s["loja_codigo"] == LOJA
        assert s["papel"] == "admin"
        assert s["usuario_id"] is not None


def test_login_senha_errada_retorna_401(banco):
    c = app.test_client()
    _signup(c)
    _aprovar_loja()
    resp = _login(c, senha="senha-errada")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Credenciais inválidas."


def test_login_usuario_inativo_retorna_401(banco):
    c = app.test_client()
    _signup(c)
    _aprovar_loja()
    conn = database.get_conn()
    conn.execute("UPDATE usuarios SET ativo=0 WHERE loja=? AND login='dona'", (LOJA,))
    conn.commit()
    conn.close()
    resp = _login(c)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Credenciais inválidas."


def _criar_operador(login="operador", senha=SENHA):
    conn = database.get_conn()
    conn.execute(
        "INSERT INTO usuarios (loja, nome, login, senha_hash, papel) VALUES (?, 'Operador', ?, ?, 'operador')",
        (LOJA, login, generate_password_hash(senha)),
    )
    conn.commit()
    conn.close()


def test_operador_recebe_403_no_pix(banco):
    c = app.test_client()
    _signup(c)
    _aprovar_loja()
    _criar_operador()
    resp = _login(c, login="operador")
    assert resp.status_code == 200
    with c.session_transaction() as s:
        assert s["papel"] == "operador"

    resp = c.post("/pix", json={"pix_chave": "chave@pix"})
    assert resp.status_code == 403


def test_admin_consegue_salvar_pix(banco):
    c = app.test_client()
    _signup(c)
    _aprovar_loja()
    resp = _login(c)
    assert resp.status_code == 200

    resp = c.post("/pix", json={"pix_chave": "chave@pix"})
    assert resp.status_code == 200

    conn = database.get_conn()
    pix = conn.execute("SELECT pix_chave FROM lojas WHERE codigo=?", (LOJA,)).fetchone()
    conn.close()
    assert pix["pix_chave"] == "chave@pix"
