import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent

TEST_SECRET = "secret-de-teste"
TEST_ADMIN_TOKEN = "token-admin-de-teste"


def _importar_app(env_overrides, codigo="import app"):
    """Importa o app em um subprocesso com ambiente controlado."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SECRET_KEY", "ADMIN_TOKEN", "SESSION_COOKIE_SECURE")
    }
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_boot_falha_sem_secret_key():
    result = _importar_app({"ADMIN_TOKEN": TEST_ADMIN_TOKEN})
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_boot_falha_sem_admin_token():
    result = _importar_app({"SECRET_KEY": TEST_SECRET})
    assert result.returncode != 0
    assert "ADMIN_TOKEN" in result.stderr


_ENV_BASE = {"SECRET_KEY": TEST_SECRET, "ADMIN_TOKEN": TEST_ADMIN_TOKEN}
_CODIGO_COOKIE = "import app; print(app.app.config['SESSION_COOKIE_SECURE'])"


def test_cookie_secure_default_true():
    result = _importar_app(_ENV_BASE, codigo=_CODIGO_COOKIE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_cookie_secure_false_para_teste_local():
    result = _importar_app(
        {**_ENV_BASE, "SESSION_COOKIE_SECURE": "false"}, codigo=_CODIGO_COOKIE
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_cookie_secure_aceita_true_maiusculo():
    result = _importar_app(
        {**_ENV_BASE, "SESSION_COOKIE_SECURE": "TRUE"}, codigo=_CODIGO_COOKIE
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_cookie_secure_valor_invalido_permanece_seguro():
    # Fail-safe: só "false" explícito desliga; typo mantém o cookie seguro.
    result = _importar_app(
        {**_ENV_BASE, "SESSION_COOKIE_SECURE": "ture"}, codigo=_CODIGO_COOKIE
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


@pytest.fixture(scope="module")
def client():
    os.environ["SECRET_KEY"] = TEST_SECRET
    os.environ["ADMIN_TOKEN"] = TEST_ADMIN_TOKEN
    sys.path.insert(0, str(BACKEND_DIR))
    import database

    # Banco temporário próprio: o DB_PATH ativo pode ser o de outro módulo
    # de teste (o último a ser coletado), possivelmente sem schema criado.
    tmp_db = Path(__file__).parent / f"_test_{uuid.uuid4().hex}.db"
    database.DB_PATH = str(tmp_db)
    database.init_db()

    import app as app_module

    app_module.app.config.update(TESTING=True)
    yield app_module.app.test_client()
    tmp_db.unlink(missing_ok=True)


def test_admin_sem_header_retorna_403(client):
    resp = client.get("/admin/lojas")
    assert resp.status_code == 403


def test_admin_token_errado_retorna_403(client):
    resp = client.get("/admin/lojas", headers={"Authorization": "token-errado"})
    assert resp.status_code == 403


def test_admin_token_correto_retorna_200(client):
    resp = client.get("/admin/lojas", headers={"Authorization": TEST_ADMIN_TOKEN})
    assert resp.status_code == 200
