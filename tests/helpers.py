"""Helpers to build test rows and to act as an application role.

Rows are inserted as the superuser of the test connection. act_as() then
switches the transaction to an application role and sets the session
variables exactly as the backend unit of work does (set_config(..., true)).
"""

import hashlib
from dataclasses import dataclass, replace
from uuid import UUID

import psycopg

APP_ROLE = "insumap_app"
READONLY_ROLE = "insumap_readonly"


def digest(value: str) -> str:
    """Return a 64-character hexadecimal SHA-256, like the stored hashes."""
    return hashlib.sha256(value.encode()).hexdigest()


def act_as(
    conn: psycopg.Connection,
    sql_role: str = APP_ROLE,
    *,
    clinic_id: UUID | None = None,
    user_id: UUID | None = None,
    role: str | None = None,
) -> None:
    """Switch to an SQL role and set app.clinic_id, app.user_id and app.role."""
    conn.execute(f"SET LOCAL ROLE {sql_role}")
    conn.execute(
        "SELECT set_config('app.clinic_id', %s, true),"
        "       set_config('app.user_id', %s, true),"
        "       set_config('app.role', %s, true)",
        (
            str(clinic_id) if clinic_id else "",
            str(user_id) if user_id else "",
            role or "",
        ),
    )


def act_as_superuser(conn: psycopg.Connection) -> None:
    """Go back to the superuser of the test connection and clear the context."""
    conn.execute("RESET ROLE")
    conn.execute(
        "SELECT set_config('app.clinic_id', '', true),"
        "       set_config('app.user_id', '', true),"
        "       set_config('app.role', '', true)"
    )


def scalar(
    conn: psycopg.Connection, sql: str, params: tuple[object, ...] = ()
) -> object:
    """Run a query and return the first column of the first row."""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def count(conn: psycopg.Connection, table: str) -> int:
    """Count the rows of a table visible to the current role."""
    value = scalar(conn, f"SELECT count(*) FROM {table}")
    assert isinstance(value, int)
    return value


def make_clinic(conn: psycopg.Connection, name: str, *, is_demo: bool = False) -> UUID:
    """Insert a clinic and return its id."""
    value = scalar(
        conn,
        "INSERT INTO clinic.clinics (name, tax_id, timezone, is_demo)"
        " VALUES (%s, '900000000-1', 'America/Bogota', %s) RETURNING id",
        (name, is_demo),
    )
    assert isinstance(value, UUID)
    return value


def make_user(conn: psycopg.Connection, email: str | None = None) -> UUID:
    """Insert an ACTIVE user (or IMPORTED without email) and return its id."""
    status = "ACTIVE" if email else "IMPORTED"
    value = scalar(
        conn,
        "INSERT INTO iam.users (email, email_normalized, password_hash, status)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (
            email,
            email,
            "$argon2id$v=19$m=19456,t=2,p=1$test" if email else None,
            status,
        ),
    )
    assert isinstance(value, UUID)
    return value


def add_membership(
    conn: psycopg.Connection, user_id: UUID, clinic_id: UUID | None, role_code: str
) -> None:
    """Link a user with a clinic and a seeded role."""
    conn.execute(
        "INSERT INTO iam.memberships (user_id, clinic_id, role_id)"
        " SELECT %s, %s, id FROM iam.roles WHERE code = %s",
        (user_id, clinic_id, role_code),
    )


def make_patient(
    conn: psycopg.Connection, clinic_id: UUID, user_id: UUID, mrn: str
) -> UUID:
    """Insert a patient with dummy document material and return its id."""
    value = scalar(
        conn,
        "INSERT INTO clinic.patients (clinic_id, user_id, mrn, full_name,"
        " birth_date, document_type, document_hash, document_hash_key_id,"
        " document_ciphertext, document_key_id, document_last4)"
        " VALUES (%s, %s, %s, %s, '1980-01-01', 'CC', %s, %s, %s, %s, '1234')"
        " RETURNING id",
        (
            clinic_id,
            user_id,
            mrn,
            f"Paciente {mrn}",
            digest(f"{clinic_id}{mrn}"),
            "0" * 16,
            b"ciphertext",
            "0" * 16,
        ),
    )
    assert isinstance(value, UUID)
    return value


def make_notice(conn: psycopg.Connection, clinic_id: UUID, published_by: UUID) -> UUID:
    """Insert the current privacy notice version of a clinic."""
    value = scalar(
        conn,
        "INSERT INTO clinic.privacy_notice_versions (clinic_id, version, content,"
        " content_sha256, effective_from, is_current, published_by)"
        " VALUES (%s, '2026-11-01', 'Aviso', %s, now(), true, %s) RETURNING id",
        (clinic_id, digest("Aviso"), published_by),
    )
    assert isinstance(value, UUID)
    return value


def make_consent(
    conn: psycopg.Connection,
    clinic_id: UUID,
    patient_id: UUID,
    notice_id: UUID,
    recorded_by: UUID,
) -> UUID:
    """Insert an accepted consent and return its id."""
    value = scalar(
        conn,
        "INSERT INTO clinic.patient_consents (clinic_id, patient_id,"
        " privacy_notice_version_id, scope, action, recorded_by)"
        " VALUES (%s, %s, %s, 'SENSITIVE_DATA_PROCESSING', 'ACCEPTED', %s)"
        " RETURNING id",
        (clinic_id, patient_id, notice_id, recorded_by),
    )
    assert isinstance(value, UUID)
    return value


def add_staff_rows(
    conn: psycopg.Connection,
    key: str,
    clinic_id: UUID,
    admin_id: UUID,
    doctor_id: UUID,
) -> None:
    """Add one row per staff-side table so every RLS table has data."""
    for user_id, name in ((admin_id, "Admin"), (doctor_id, "Doctor")):
        conn.execute(
            "INSERT INTO clinic.staff_profiles (user_id, clinic_id, full_name)"
            " VALUES (%s, %s, %s)",
            (user_id, clinic_id, f"{name} {key}"),
        )
    conn.execute(
        "INSERT INTO clinic.import_batches (clinic_id, source_format, file_sha256,"
        " file_size_bytes, created_by) VALUES (%s, 'CSV', %s, 10, %s)",
        (clinic_id, digest(f"file-{key}"), admin_id),
    )
    conn.execute(
        "INSERT INTO iam.credential_invitations (clinic_id, user_id, purpose,"
        " channel, code_hash, code_key_id, expires_at, issued_by)"
        " VALUES (%s, %s, 'STAFF_ACTIVATION', 'EMAIL', %s, %s,"
        " now() + interval '7 days', %s)",
        (clinic_id, doctor_id, digest(f"staff-code-{key}"), "0" * 16, admin_id),
    )
    conn.execute(
        "INSERT INTO iam.refresh_tokens (user_id, clinic_id, family_id, token_hash,"
        " expires_at, family_expires_at)"
        " VALUES (%s, %s, uuidv7(), %s, now() + interval '12 hours',"
        " now() + interval '12 hours')",
        (doctor_id, clinic_id, digest(f"staff-refresh-{key}")),
    )
    conn.execute(
        "INSERT INTO iam.mfa_factors (user_id, factor_type, secret_ciphertext,"
        " key_id, confirmed_at) VALUES (%s, 'TOTP', 'secret', %s, now())",
        (doctor_id, "0" * 16),
    )
    conn.execute(
        "INSERT INTO iam.password_history (user_id, password_hash) VALUES (%s, %s)",
        (doctor_id, "$argon2id$v=19$m=19456,t=2,p=1$old"),
    )
    conn.execute(
        "INSERT INTO iam.security_events (event_type, severity, user_id, clinic_id)"
        " VALUES ('ROLE_CHANGED', 'LOW', %s, %s)",
        (doctor_id, clinic_id),
    )


def add_clinic_rows(
    conn: psycopg.Connection, key: str, tenant: "Tenant"
) -> tuple[UUID, UUID]:
    """Add one row per remaining clinic table; return the site and assignment."""
    site_id = scalar(
        conn,
        "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, %s) RETURNING id",
        (tenant.clinic_id, f"Sede {key}"),
    )
    conn.execute(
        "INSERT INTO clinic.staff_sites (user_id, site_id, clinic_id)"
        " VALUES (%s, %s, %s)",
        (tenant.doctor_id, site_id, tenant.clinic_id),
    )
    assignment_id = scalar(
        conn,
        "INSERT INTO clinic.care_team_assignments (clinic_id, patient_id,"
        " doctor_user_id, is_primary, assigned_by) VALUES (%s, %s, %s, true, %s)"
        " RETURNING id",
        (tenant.clinic_id, tenant.patient_id, tenant.doctor_id, tenant.admin_id),
    )
    conn.execute(
        "INSERT INTO clinic.privacy_requests (clinic_id, patient_id, request_type,"
        " due_at) VALUES (%s, %s, 'ACCESS', now() + interval '15 days')",
        (tenant.clinic_id, tenant.patient_id),
    )
    batch_id = scalar(
        conn,
        "SELECT id FROM clinic.import_batches WHERE clinic_id = %s",
        (tenant.clinic_id,),
    )
    conn.execute(
        "INSERT INTO clinic.import_rows (clinic_id, batch_id, row_number, status)"
        " VALUES (%s, %s, 1, 'CREATED')",
        (tenant.clinic_id, batch_id),
    )
    conn.execute(
        "INSERT INTO clinic.clinic_settings (clinic_id, recommendation_expiry_hours,"
        " forecast_refresh_minutes, dropout_inactivity_days, kpi_min_cell_size)"
        " VALUES (%s, 1, 1, 1, 1)",
        (tenant.clinic_id,),
    )
    assert isinstance(site_id, UUID)
    assert isinstance(assignment_id, UUID)
    return site_id, assignment_id


@dataclass(frozen=True)
class Tenant:
    """A clinic with one person per role and one patient."""

    clinic_id: UUID
    admin_id: UUID
    doctor_id: UUID
    patient_user_id: UUID
    patient_id: UUID
    notice_id: UUID
    consent_id: UUID
    site_id: UUID
    assignment_id: UUID


def make_tenant(conn: psycopg.Connection, key: str) -> Tenant:
    """Build a complete clinic as the superuser."""
    clinic_id = make_clinic(conn, f"Clinica {key}")
    admin_id = make_user(conn, f"admin-{key}@example.test")
    add_membership(conn, admin_id, clinic_id, "CLINIC_ADMIN")
    doctor_id = make_user(conn, f"doctor-{key}@example.test")
    add_membership(conn, doctor_id, clinic_id, "DOCTOR")
    patient_user_id = make_user(conn, f"patient-{key}@example.test")
    add_membership(conn, patient_user_id, clinic_id, "PATIENT")
    patient_id = make_patient(conn, clinic_id, patient_user_id, f"MRN-{key}")
    notice_id = make_notice(conn, clinic_id, admin_id)
    consent_id = make_consent(conn, clinic_id, patient_id, notice_id, patient_user_id)
    add_staff_rows(conn, key, clinic_id, admin_id, doctor_id)
    base = Tenant(
        clinic_id=clinic_id,
        admin_id=admin_id,
        doctor_id=doctor_id,
        patient_user_id=patient_user_id,
        patient_id=patient_id,
        notice_id=notice_id,
        consent_id=consent_id,
        site_id=clinic_id,
        assignment_id=clinic_id,
    )
    site_id, assignment_id = add_clinic_rows(conn, key, base)
    return replace(base, site_id=site_id, assignment_id=assignment_id)
