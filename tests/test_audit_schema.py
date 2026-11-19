"""Audit tables: hash chain per clinic, append-only logs, RLS and retention."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    make_tenant,
    scalar,
)

APPEND = "SELECT audit.append_audit_log(%s, %s)"
ACCESS_INSERT = (
    "INSERT INTO audit.data_access_log (clinic_id, patient_id, actor_user_id,"
    " actor_role, actor_type, resource_type, outcome, purpose)"
    " VALUES (%s, %s, %s, 'DOCTOR', 'USER', 'GLUCOSE_READINGS', 'GRANTED', 'CARE')"
)
GENESIS = "0" * 64


@pytest.fixture
def tenants(db: psycopg.Connection) -> tuple[Tenant, Tenant]:
    return make_tenant(db, "a"), make_tenant(db, "b")


def _append(db: psycopg.Connection, t: Tenant, action: str) -> None:
    act_as(db, clinic_id=t.clinic_id, user_id=t.admin_id, role="CLINIC_ADMIN")
    db.execute(APPEND, (action, "test_entity"))
    act_as_superuser(db)


def test_tables_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'audit' ORDER BY table_name"
    ).fetchall()
    assert [r[0] for r in rows] == [
        "audit_log",
        "data_access_log",
        "retention_policies",
    ]


def test_append_builds_a_chain_per_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    _append(db, a, "PATIENT_ACTIVATED")
    _append(db, a, "TREATMENT_PLAN_ACTIVATED")
    _append(db, b, "PATIENT_ACTIVATED")

    rows = db.execute(
        "SELECT chain_seq, prev_hash, row_hash, actor_user_id, actor_type"
        " FROM audit.audit_log WHERE clinic_id = %s ORDER BY chain_seq",
        (a.clinic_id,),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == GENESIS
    assert rows[1][1] == rows[0][2]
    assert rows[0][3] == a.admin_id
    assert rows[0][4] == "USER"
    other = scalar(
        db, "SELECT chain_seq FROM audit.audit_log WHERE clinic_id = %s", (b.clinic_id,)
    )
    assert other == 1


def test_verify_chain_is_empty_when_intact_and_finds_tampering(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    for action in ("A_ONE", "A_TWO", "A_THREE"):
        _append(db, a, action)
    assert (
        db.execute("SELECT * FROM audit.verify_chain(%s)", (a.clinic_id,)).fetchall()
        == []
    )

    db.execute("ALTER TABLE audit.audit_log DISABLE TRIGGER trg_audit_log_append_only")
    db.execute(
        "UPDATE audit.audit_log SET action = 'TAMPERED'"
        " WHERE clinic_id = %s AND chain_seq = 2",
        (a.clinic_id,),
    )
    db.execute("ALTER TABLE audit.audit_log ENABLE TRIGGER trg_audit_log_append_only")
    broken = db.execute(
        "SELECT * FROM audit.verify_chain(%s)", (a.clinic_id,)
    ).fetchall()
    assert [r[0] for r in broken] == [2]


def test_app_cannot_insert_audit_log_directly(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute(
            "INSERT INTO audit.audit_log (clinic_id, chain_seq, actor_type, action,"
            " entity_type, prev_hash, row_hash)"
            " VALUES (%s, 1, 'USER', 'X', 'y', %s, %s)",
            (a.clinic_id, GENESIS, GENESIS),
        )


def test_append_needs_a_clinic_context(db: psycopg.Connection) -> None:
    act_as(db)
    with pytest.raises(errors.RaiseException):
        db.execute(APPEND, ("NO_CLINIC", "test_entity"))


@pytest.mark.parametrize("table", ["audit_log", "data_access_log"])
def test_logs_are_append_only(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    a, _ = tenants
    _append(db, a, "SOMETHING")
    db.execute(ACCESS_INSERT, (a.clinic_id, a.patient_id, a.doctor_id))
    for statement in (
        f"UPDATE audit.{table} SET actor_type = 'SYSTEM'",
        f"DELETE FROM audit.{table}",
    ):
        with pytest.raises(errors.RaiseException), db.transaction():
            db.execute(statement)


def test_access_log_insert_and_read_are_isolated_by_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(ACCESS_INSERT, (a.clinic_id, a.patient_id, a.doctor_id))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(ACCESS_INSERT, (b.clinic_id, b.patient_id, a.doctor_id))
    assert count(db, "audit.data_access_log") == 0  # a doctor cannot read it

    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "audit.data_access_log") == 1
    act_as(db, clinic_id=b.clinic_id, user_id=b.admin_id, role="CLINIC_ADMIN")
    assert count(db, "audit.data_access_log") == 0


def test_audit_log_is_read_only_by_the_clinic_admin(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    _append(db, a, "SOMETHING")
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "audit.audit_log") == 1
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert count(db, "audit.audit_log") == 0
    act_as(db, clinic_id=b.clinic_id, user_id=b.admin_id, role="CLINIC_ADMIN")
    assert count(db, "audit.audit_log") == 0
    act_as(db, role="PLATFORM_ADMIN")
    assert count(db, "audit.audit_log") == 0


def test_retention_policy_changes_only_through_the_function(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO audit.retention_policies (table_name, reference_column)"
            " VALUES ('iam.login_attempts', 'occurred_at')"
        )
    db.execute(
        "SELECT audit.set_retention_policy('iam.login_attempts', 'occurred_at', 90,"
        " 'DELETE', true, 'Decisión técnica de prueba', 'Motivo de prueba largo')"
    )
    assert count(db, "audit.retention_policies") == 1
    act_as_superuser(db)
    action = scalar(
        db,
        "SELECT action FROM audit.audit_log WHERE clinic_id = %s",
        (a.clinic_id,),
    )
    assert action == "RETENTION_POLICY_CHANGED"


def test_doctor_cannot_change_retention(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.RaiseException):
        db.execute(
            "SELECT audit.set_retention_policy('iam.login_attempts', 'occurred_at',"
            " 90, 'DELETE', true, NULL, 'Motivo de prueba largo')"
        )


def test_app_role_used_by_the_tests_is_the_application_role() -> None:
    assert APP_ROLE == "insumap_app"
