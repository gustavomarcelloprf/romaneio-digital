# Fixtures compartilhadas da suíte: os testes rodam contra um Postgres de
# TESTE (TEST_DATABASE_URL) e cada teste começa com o schema recriado do zero.
#
# ATENÇÃO: o banco apontado por TEST_DATABASE_URL é APAGADO a cada teste
# (DROP SCHEMA public CASCADE). Nunca aponte para o banco de dev/produção.
import os
import sys
from pathlib import Path

import psycopg
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.exit(
        "TEST_DATABASE_URL não definida. Aponte-a para um Postgres de TESTE "
        "(ex.: postgresql://localhost:5432/romaneio_test); o schema desse banco "
        "é recriado a cada teste.",
        returncode=4,
    )

# app.py exige SECRET_KEY, ADMIN_TOKEN e DATABASE_URL no import (boot
# fail-fast) e chama init_db(). O conftest é importado antes de qualquer
# módulo de teste, então o app e o database.py já nascem apontando para o
# banco de teste.
os.environ.setdefault("SECRET_KEY", "secret-de-teste")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-teste")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import database  # noqa: E402


def limpar_schema():
    """Zera o banco de teste: DROP SCHEMA public CASCADE + CREATE SCHEMA public."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


def resetar_banco():
    """Schema zerado e recriado por init_db(): o estado inicial de cada teste."""
    limpar_schema()
    database.init_db()


@pytest.fixture(autouse=True)
def _banco_limpo():
    resetar_banco()
    yield


@pytest.fixture()
def banco_sem_schema():
    """Banco vazio SEM init_db(): para testes que montam um schema antigo à mão."""
    limpar_schema()
    yield
