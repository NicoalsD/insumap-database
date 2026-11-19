"""clinic.privacy_notice_for_login(): the public text of a notice, without session."""

from uuid import UUID

import psycopg
import pytest

from tests.helpers import Tenant, act_as, make_tenant

NOTICE = (
    "SELECT id, version, effective_from, content"
    " FROM clinic.privacy_notice_for_login(%s)"
)


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "aviso")


def test_an_anonymous_session_reads_the_notice_text(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(db)

    row = db.execute(NOTICE, (tenant.notice_id,)).fetchone()

    assert row is not None
    assert row[0] == tenant.notice_id
    assert row[3]


def test_the_table_stays_hidden_without_a_clinic(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(db)

    rows = db.execute("SELECT id FROM clinic.privacy_notice_versions").fetchall()

    assert rows == []


def test_an_unknown_id_returns_nothing(db: psycopg.Connection) -> None:
    act_as(db)

    assert db.execute(NOTICE, (UUID(int=1),)).fetchall() == []
