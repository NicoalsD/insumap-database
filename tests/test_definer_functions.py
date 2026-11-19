"""SECURITY DEFINER lookups and the clinic suspension trigger."""

from uuid import UUID

import psycopg
import pytest

from tests.helpers import Tenant, act_as, act_as_superuser, digest, make_tenant, scalar


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "definer")


def test_login_lookup_works_without_context(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(db)
    assert scalar(db, "SELECT count(*) FROM iam.users") == 0
    row = db.execute(
        "SELECT user_id, status, role, clinic_id, clinic_status, token_version,"
        "       password_hash IS NOT NULL"
        "  FROM iam.lookup_login_identity('doctor-definer@example.test')"
    ).fetchone()
    assert row == (
        tenant.doctor_id,
        "ACTIVE",
        "DOCTOR",
        tenant.clinic_id,
        "ACTIVE",
        0,
        True,
    )
    unknown = db.execute(
        "SELECT * FROM iam.lookup_login_identity('nobody@example.test')"
    ).fetchall()
    assert unknown == []


def test_invitation_lookup_by_code_hash(db: psycopg.Connection, tenant: Tenant) -> None:
    invitation_id = scalar(
        db,
        "INSERT INTO iam.credential_invitations (clinic_id, user_id, patient_id,"
        " purpose, channel, code_hash, code_key_id, expires_at, issued_by)"
        " VALUES (%s, %s, %s, 'PATIENT_ACTIVATION', 'PRINT', %s, %s,"
        " now() + interval '7 days', %s) RETURNING id",
        (
            tenant.clinic_id,
            tenant.patient_user_id,
            tenant.patient_id,
            digest("code"),
            "0" * 16,
            tenant.admin_id,
        ),
    )
    act_as(db)
    row = db.execute(
        "SELECT invitation_id, clinic_id, user_id, purpose, status, failed_attempts"
        "  FROM iam.lookup_invitation_by_code_hash(%s)",
        (digest("code"),),
    ).fetchone()
    assert row == (
        invitation_id,
        tenant.clinic_id,
        tenant.patient_user_id,
        "PATIENT_ACTIVATION",
        "PENDING",
        0,
    )
    malformed = db.execute(
        "SELECT * FROM iam.lookup_invitation_by_code_hash('not-a-hash')"
    ).fetchall()
    assert malformed == []


def test_refresh_lookup_and_suspension_revokes_families(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    family_id = scalar(db, "SELECT uuidv7()")
    db.execute(
        "INSERT INTO iam.refresh_tokens (user_id, clinic_id, family_id, token_hash,"
        " expires_at, family_expires_at)"
        " VALUES (%s, %s, %s, %s, now() + interval '30 days',"
        " now() + interval '90 days')",
        (tenant.patient_user_id, tenant.clinic_id, family_id, digest("refresh")),
    )
    act_as(db)
    row = db.execute(
        "SELECT user_id, clinic_id, role, family_id, revoked_at"
        "  FROM iam.lookup_refresh_token(%s)",
        (digest("refresh"),),
    ).fetchone()
    assert row == (tenant.patient_user_id, tenant.clinic_id, "PATIENT", family_id, None)

    act_as_superuser(db)
    platform_admin = scalar(
        db,
        "INSERT INTO iam.users (email, email_normalized, status)"
        " VALUES ('pa@example.test', 'pa@example.test', 'ACTIVE') RETURNING id",
    )
    assert isinstance(platform_admin, UUID)
    act_as(db, user_id=platform_admin, role="PLATFORM_ADMIN")
    db.execute(
        "UPDATE clinic.clinics SET status = 'SUSPENDED', suspended_at = now(),"
        " suspended_reason = 'Contrato vencido' WHERE id = %s",
        (tenant.clinic_id,),
    )
    act_as_superuser(db)
    assert (
        scalar(
            db,
            "SELECT revoked_reason FROM iam.refresh_tokens WHERE family_id = %s",
            (family_id,),
        )
        == "CLINIC_SUSPENDED"
    )
