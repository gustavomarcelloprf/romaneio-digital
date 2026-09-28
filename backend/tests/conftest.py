# Fixtures compartilhadas da suíte: os testes rodam contra um Postgres de
# TESTE (TEST_DATABASE_URL) e cada teste começa com o banco zerado.
#
# ATENÇÃO: o banco apontado por TEST_DATABASE_URL é APAGADO entre os testes
# (TRUNCATE de todas as tabelas; o schema em si só é (re)criado uma vez por
# sessão, ou quando um teste pede o banco sem schema). Nunca aponte para o
# banco de dev/produção.
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


def truncar_todas_tabelas():
    """TRUNCATE de todas as tabelas do schema public (RESTART IDENTITY CASCADE).

    Bem mais rápido que recriar o schema do zero a cada teste, mas depende de
    a estrutura já existir — por isso o schema é criado uma vez por sessão
    (fixture `_schema_da_sessao`) em vez de a cada teste.
    """
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        tabelas = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()]
        if tabelas:
            nomes = ", ".join(f'"{t}"' for t in tabelas)
            conn.execute(f"TRUNCATE TABLE {nomes} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session", autouse=True)
def _schema_da_sessao():
    """Cria o schema (via init_db()) uma única vez por sessão de testes."""
    resetar_banco()
    yield


@pytest.fixture(autouse=True)
def _banco_limpo():
    truncar_todas_tabelas()
    yield


@pytest.fixture()
def banco_sem_schema():
    """Banco vazio SEM init_db(): para testes que montam um schema antigo à mão.

    Ao fim do teste o schema completo é recriado: esse teste monta tabelas com
    colunas faltando de propósito, e deixá-las assim corromperia o TRUNCATE
    dos testes seguintes (colunas que a aplicação espera não existiriam mais).
    """
    limpar_schema()
    yield
    resetar_banco()
