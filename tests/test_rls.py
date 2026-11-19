"""Row level security: clinic isolation, own rows and fail-closed behavior."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    READONLY_ROLE,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    digest,
    make_tenant,
    make_user,
    scalar,
)

RLS_TABLES = (
    "iam.users",
    "iam.memberships",
    "iam.credential_invitations",
    "iam.refresh_tokens",
    "iam.mfa_factors",
    "iam.password_history",
    "iam.security_events",
    "clinic.clinics",
    "clinic.import_batches",
    "clinic.staff_profiles",
    "clinic.patients",
    "clinic.privacy_notice_versions",
    "clinic.patient_consents",
)
# RLS-4 (Modelo de datos 4.3): located by a 256-bit token before any clinic is
# known; its clinic_id is informative.
CLINIC_ID_WITHOUT_RLS = {"iam.auth_challenges"}
MEMBERS_PER_TENANT = 3


@pytest.fixture
def tenants(db: psycopg.Connection) -> tuple[Tenant, Tenant]:
    """Two complete clinics, built as the superuser."""
    return make_tenant(db, "a"), make_tenant(db, "b")


def test_every_table_with_clinic_id_forces_rls(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT n.nspname || '.' || c.relname, c.relrowsecurity,"
        "       c.relforcerowsecurity"
        "  FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace"
        " WHERE c.relkind = 'r' AND n.nspname IN ('iam', 'clinic')"
        "   AND (EXISTS (SELECT 1 FROM pg_attribute AS a WHERE a.attrelid = c.oid"
        "                 AND a.attname = 'clinic_id' AND NOT a.attisdropped)"
        "        OR c.relname = 'clinics')"
    ).fetchall()
    for table, enabled, forced in rows:
        if table in CLINIC_ID_WITHOUT_RLS:
            continue
        assert (enabled, forced) == (True, True), table


@pytest.mark.parametrize("table", RLS_TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    assert count(db, table) > 0
    act_as(db)
    assert count(db, table) == 0


@pytest.mark.parametrize("table", ["iam.users", "clinic.patients", "clinic.clinics"])
def test_readonly_without_context_sees_no_rows(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    act_as(db, READONLY_ROLE)
    assert count(db, table) == 0


def test_clinic_admin_sees_only_its_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert scalar(db, "SELECT array_agg(id) FROM clinic.clinics") == [a.clinic_id]
    assert scalar(db, "SELECT array_agg(id) FROM clinic.patients") == [a.patient_id]
    assert count(db, "iam.memberships") == MEMBERS_PER_TENANT
    assert count(db, "iam.users") == MEMBERS_PER_TENANT
    assert (
        scalar(db, "SELECT count(*) FROM iam.users WHERE id = %s", (b.admin_id,)) == 0
    )


@pytest.mark.parametrize(
    "table",
    [
        "iam.memberships",
        "iam.credential_invitations",
        "iam.refresh_tokens",
        "iam.security_events",
        "clinic.import_batches",
        "clinic.staff_profiles",
        "clinic.patients",
        "clinic.privacy_notice_versions",
        "clinic.patient_consents",
    ],
)
@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "DOCTOR", "PATIENT", "SYSTEM"])
def test_no_role_sees_rows_of_another_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str, role: str
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    foreign = scalar(
        db, f"SELECT count(*) FROM {table} WHERE clinic_id = %s", (b.clinic_id,)
    )
    assert foreign == 0


def test_cannot_write_into_another_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO clinic.privacy_notice_versions (clinic_id, version, content,"
            " content_sha256, effective_from, published_by)"
            " VALUES (%s, 'x', 'x', %s, now(), %s)",
            (b.clinic_id, digest("x"), a.admin_id),
        )
    updated = db.execute(
        "UPDATE clinic.patients SET full_name = 'X' WHERE id = %s", (b.patient_id,)
    ).rowcount
    assert updated == 0


def test_doctor_cannot_insert_patients(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert count(db, "clinic.patients") == 1
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO clinic.patients (clinic_id, user_id, mrn, full_name,"
            " birth_date, document_type, document_hash, document_hash_key_id,"
            " document_ciphertext, document_key_id, document_last4)"
            " VALUES (%s, %s, 'M', 'N', '1990-01-01', 'CC', %s, %s, 'x', %s, '1')",
            (a.clinic_id, a.doctor_id, digest("m"), "0" * 16, "0" * 16),
        )


def test_patient_sees_only_own_rows(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, "SELECT clinic.current_patient_id()") == a.patient_id
    assert scalar(db, "SELECT array_agg(id) FROM clinic.patients") == [a.patient_id]
    assert scalar(db, "SELECT array_agg(id) FROM iam.users") == [a.patient_user_id]
    assert scalar(db, "SELECT array_agg(id) FROM clinic.patient_consents") == [
        a.consent_id
    ]
    assert count(db, "clinic.staff_profiles") == 0


def test_patient_inserts_only_own_consent(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    insert = (
        "INSERT INTO clinic.patient_consents (clinic_id, patient_id,"
        " privacy_notice_version_id, scope, action, recorded_by)"
        " VALUES (%s, %s, %s, 'AI_EXTERNAL_PROCESSING', 'ACCEPTED', %s)"
    )
    db.execute(insert, (a.clinic_id, a.patient_id, a.notice_id, a.patient_user_id))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, b.patient_id, b.notice_id, a.patient_user_id))


def test_platform_admin_manages_clinics_but_sees_no_patients(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    platform_admin = make_user(db, "platform@example.test")
    db.execute(
        "INSERT INTO iam.memberships (user_id, clinic_id, role_id)"
        " SELECT %s, NULL, id FROM iam.roles WHERE code = 'PLATFORM_ADMIN'",
        (platform_admin,),
    )
    act_as(db, user_id=platform_admin, role="PLATFORM_ADMIN")
    assert count(db, "clinic.clinics") == len(tenants)
    assert count(db, "clinic.patients") == 0
    assert count(db, "clinic.patient_consents") == 0
    assert scalar(db, "SELECT array_agg(id) FROM iam.users") == [platform_admin]


def test_security_events_accept_inserts_without_context(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    visible_before = count(db, "iam.security_events")
    act_as(db)
    db.execute(
        "INSERT INTO iam.security_events (event_type, severity)"
        " VALUES ('LOGIN_FAILED', 'LOW')"
    )
    assert count(db, "iam.security_events") == 0
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO iam.security_events (event_type, severity, clinic_id)"
            " VALUES ('PERMISSION_DENIED', 'MEDIUM', %s)",
            (b.clinic_id,),
        )
    db.execute(
        "INSERT INTO iam.security_events (event_type, severity, clinic_id)"
        " VALUES ('PERMISSION_DENIED', 'MEDIUM', %s)",
        (a.clinic_id,),
    )
    assert count(db, "iam.security_events") == 0
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "iam.security_events") == visible_before + 1


def test_activation_context_reaches_own_invitation_only(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    insert = (
        "INSERT INTO iam.credential_invitations (clinic_id, user_id, patient_id,"
        " purpose, channel, code_hash, code_key_id, expires_at, issued_by)"
        " VALUES (%s, %s, %s, 'PATIENT_ACTIVATION', 'PRINT', %s, %s,"
        " now() + interval '7 days', %s) RETURNING id"
    )
    own = scalar(
        db,
        insert,
        (
            a.clinic_id,
            a.patient_user_id,
            a.patient_id,
            digest("a"),
            "0" * 16,
            a.admin_id,
        ),
    )
    scalar(
        db,
        insert,
        (
            b.clinic_id,
            b.patient_user_id,
            b.patient_id,
            digest("b"),
            "0" * 16,
            b.admin_id,
        ),
    )
    # Provisional context after iam.lookup_invitation_by_code_hash: no role yet.
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id)
    assert scalar(db, "SELECT array_agg(id) FROM iam.credential_invitations") == [own]
    updated = db.execute(
        "UPDATE iam.credential_invitations SET status = 'VERIFIED', verified_at = now()"
    ).rowcount
    assert updated == 1
    act_as_superuser(db)
    assert (
        scalar(
            db,
            "SELECT count(*) FROM iam.credential_invitations WHERE status = 'VERIFIED'",
        )
        == 1
    )
