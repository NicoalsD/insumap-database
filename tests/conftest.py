"""Shared fixtures for the Insumap schema tests.

The tests need a clean PostgreSQL 18 database reachable through
DATABASE_URL_MIGRATOR. When that variable is not set, the tests are skipped.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR_URL_VAR = "DATABASE_URL_MIGRATOR"


@pytest.fixture(scope="session")
def migrator_url() -> str:
    """Return the migrator connection URL, or skip the integration tests."""
    url = os.environ.get(MIGRATOR_URL_VAR)
    if not url:
        pytest.skip(f"{MIGRATOR_URL_VAR} is not set; schema tests need PostgreSQL 18.")
    return url


@pytest.fixture(scope="session")
def alembic_config(migrator_url: str) -> Config:
    """Build an Alembic config that uses absolute paths, independent of cwd.

    Depends on migrator_url so the tests skip before Alembic touches anything.
    """
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


@pytest.fixture(scope="session")
def schema_connection(
    alembic_config: Config, migrator_url: str
) -> Iterator[psycopg.Connection]:
    """Migrate the database to head and yield a psycopg connection to it."""
    command.upgrade(alembic_config, "head")
    with psycopg.connect(migrator_url, autocommit=True) as connection:
        yield connection
