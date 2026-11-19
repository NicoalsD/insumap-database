"""Append-only and immutability triggers (they also bind the superuser)."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import Tenant, act_as, make_tenant, make_user, scalar


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "append")


@pytest.fixture
def event_id(db: psycopg.Connection) -> object:
    return scalar(
        db,
        "INSERT INTO iam.security_events (event_type, severity)"
        " VALUES ('LOGIN_FAILED', 'LOW') RETURNING id",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE iam.security_events SET severity = 'HIGH' WHERE id = %s",
        "DELETE FROM iam.security_events WHERE id = %s",
    ],
)
def test_security_events_reject_mutation(
    db: psycopg.Connection, event_id: object, statement: str
) -> None:
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (event_id,))


def test_security_events_purge_may_delete(
    db: psycopg.Connection, event_id: object
) -> None:
    db.execute("SET LOCAL audit.purging = 'on'")
    deleted = db.execute(
        "DELETE FROM iam.security_events WHERE id = %s", (event_id,)
    ).rowcount
    assert deleted == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE clinic.patient_consents SET action = 'WITHDRAWN' WHERE id = %s",
        "DELETE FROM clinic.patient_consents WHERE id = %s",
    ],
)
def test_patient_consents_reject_mutation(
    db: psycopg.Connection, tenant: Tenant, statement: str
) -> None:
    db.execute("SET LOCAL audit.purging = 'on'")
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (tenant.consent_id,))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE clinic.privacy_notice_versions SET content = 'Otro' WHERE id = %s",
        "UPDATE clinic.privacy_notice_versions SET version = 'v2' WHERE id = %s",
        "DELETE FROM clinic.privacy_notice_versions WHERE id = %s",
    ],
)
def test_privacy_notice_content_is_immutable(
    db: psycopg.Connection, tenant: Tenant, statement: str
) -> None:
    with pytest.raises(errors.RaiseException), db.transaction():
        db.execute(statement, (tenant.notice_id,))


def test_privacy_notice_current_flag_may_change(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.admin_id, role="CLINIC_ADMIN")
    updated = db.execute(
        "UPDATE clinic.privacy_notice_versions SET is_current = false WHERE id = %s",
        (tenant.notice_id,),
    ).rowcount
    assert updated == 1


@pytest.mark.parametrize(
    ("clinic", "role_code"),
    [(False, "DOCTOR"), (True, "PLATFORM_ADMIN")],
)
def test_membership_clinic_matches_role(
    db: psycopg.Connection, tenant: Tenant, clinic: bool, role_code: str
) -> None:
    user_id = make_user(db, f"{role_code.lower()}@example.test")
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO iam.memberships (user_id, clinic_id, role_id)"
            " SELECT %s, %s, id FROM iam.roles WHERE code = %s",
            (user_id, tenant.clinic_id if clinic else None, role_code),
        )


def test_active_membership_has_no_end(db: psycopg.Connection, tenant: Tenant) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "UPDATE iam.memberships SET ended_at = now() WHERE user_id = %s",
            (tenant.doctor_id,),
        )
    db.execute(
        "UPDATE iam.memberships SET is_active = false, ended_at = now()"
        " WHERE user_id = %s",
        (tenant.doctor_id,),
    )
