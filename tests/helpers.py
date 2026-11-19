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


PROFILE_COLUMNS = (
    "clinic_id, code, label, population, version, status, is_default,"
    " range_low_mg_dl, range_high_mg_dl, very_low_mg_dl, very_high_mg_dl,"
    " tir_min_pct, tbr_l1_max_pct, tbr_l2_max_pct, tar_l1_max_pct, tar_l2_max_pct,"
    " cv_max_pct, min_data_sufficiency_pct, min_days, max_gap_minutes,"
    " hypo_episode_min_minutes, min_readings_for_cv, min_readings_for_cv_smbg,"
    " night_start, night_end, source_reference"
)
# Dummy values: tests never depend on a clinical meaning.
PROFILE_VALUES = (
    "%s, %s, 'Perfil de prueba', 'ADULT', %s, %s, false,"
    " 10, 20, 5, 30, 50, 10, 5, 40, 10, 40, 60, 7, 15, 10, 50, 20,"
    " '00:00', '06:00', 'Fuente de prueba'"
)


def make_profile(
    conn: psycopg.Connection,
    clinic_id: UUID | None,
    code: str = "TEST_PROFILE",
    *,
    version: int = 1,
    status: str = "DRAFT",
) -> UUID:
    """Insert a target profile (global when clinic_id is None)."""
    value = scalar(
        conn,
        f"INSERT INTO care.target_profiles ({PROFILE_COLUMNS})"
        f" VALUES ({PROFILE_VALUES}) RETURNING id",
        (clinic_id, code, version, status),
    )
    assert isinstance(value, UUID)
    return value


@dataclass(frozen=True)
class CareRows:
    """One row per care table of a tenant."""

    profile_id: UUID
    template_id: UUID
    plan_id: UUID
    item_id: UUID
    draft_plan_id: UUID
    snapshot_id: UUID
    reminder_id: UUID
    follow_up_id: UUID
    outreach_id: UUID
    note_id: UUID
    review_id: UUID
    validation_id: UUID
    symptom_id: UUID
    metrics_id: UUID


def populate_care(conn: psycopg.Connection, t: Tenant) -> CareRows:
    """Add a row to every care table of a tenant (as the superuser)."""
    profile_id = make_profile(conn, t.clinic_id, "CLINIC_PROFILE", status="ACTIVE")
    conn.execute(
        "INSERT INTO care.patient_target_assignments (clinic_id, patient_id,"
        " target_profile_id, assigned_by) VALUES (%s, %s, %s, %s)",
        (t.clinic_id, t.patient_id, profile_id, t.doctor_id),
    )
    template_id = scalar(
        conn,
        "INSERT INTO care.protocol_templates (clinic_id, code, name, version,"
        " status, content) VALUES (%s, 'TEMPLATE', 'Plantilla', 1, 'ACTIVE', '{}')"
        " RETURNING id",
        (t.clinic_id,),
    )
    plan_sql = (
        "INSERT INTO care.treatment_plans (clinic_id, patient_id, version, status,"
        " template_id, target_profile_id, created_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
    )
    plan_id = scalar(
        conn,
        plan_sql,
        (t.clinic_id, t.patient_id, 1, "ACTIVE", template_id, profile_id, t.doctor_id),
    )
    draft_plan_id = scalar(
        conn,
        plan_sql,
        (t.clinic_id, t.patient_id, 2, "DRAFT", template_id, profile_id, t.doctor_id),
    )
    item_id = scalar(
        conn,
        "INSERT INTO care.treatment_plan_items (clinic_id, plan_id, item_type, name)"
        " VALUES (%s, %s, 'MEASURE', 'Medicion') RETURNING id",
        (t.clinic_id, draft_plan_id),
    )
    snapshot_id = scalar(
        conn,
        "INSERT INTO care.treatment_plan_snapshots (clinic_id, plan_id, snapshot_no,"
        " snapshot, taken_by) VALUES (%s, %s, 1, '{}', %s) RETURNING id",
        (t.clinic_id, plan_id, t.doctor_id),
    )
    reminder_id = scalar(
        conn,
        "INSERT INTO care.reminder_schedules (clinic_id, patient_id, reminder_type,"
        " local_time, message_key, set_by)"
        " VALUES (%s, %s, 'MEASUREMENT', '08:00', 'reminder.measure', %s)"
        " RETURNING id",
        (t.clinic_id, t.patient_id, t.doctor_id),
    )
    follow_sql = (
        "INSERT INTO care.follow_ups (clinic_id, patient_id, kind, status, title,"
        " created_by) VALUES (%s, %s, %s, %s, 'Control', %s) RETURNING id"
    )
    follow_up_id = scalar(
        conn,
        follow_sql,
        (t.clinic_id, t.patient_id, "CONTROL", "SCHEDULED", t.doctor_id),
    )
    outreach_id = scalar(
        conn, follow_sql, (t.clinic_id, t.patient_id, "OUTREACH", "OPEN", t.admin_id)
    )
    note_id = scalar(
        conn,
        "INSERT INTO care.clinical_notes (clinic_id, patient_id, author_user_id, body)"
        " VALUES (%s, %s, %s, 'Nota') RETURNING id",
        (t.clinic_id, t.patient_id, t.doctor_id),
    )
    review_id = scalar(
        conn,
        "INSERT INTO care.triage_reviews (clinic_id, patient_id, reviewed_by,"
        " window_days) VALUES (%s, %s, %s, 7) RETURNING id",
        (t.clinic_id, t.patient_id, t.doctor_id),
    )
    validation_id = scalar(
        conn,
        "INSERT INTO care.reading_validation_settings (clinic_id, version, status,"
        " min_measurable_mg_dl, max_measurable_mg_dl, future_tolerance_seconds,"
        " max_reading_age_hours, duplicate_window_seconds, max_rate_mg_dl_per_min,"
        " unit_mismatch_ratio) VALUES (%s, 1, 'ACTIVE', 10, 900, 60, 48, 120, 9, 5)"
        " RETURNING id",
        (t.clinic_id,),
    )
    symptom_id = scalar(
        conn,
        "INSERT INTO care.symptom_catalog (clinic_id, code, label_es)"
        " VALUES (%s, 'TEST_SYMPTOM', 'Sintoma') RETURNING id",
        (t.clinic_id,),
    )
    metrics_id = scalar(
        conn,
        "INSERT INTO care.glycemic_metric_snapshots (clinic_id, patient_id,"
        " target_profile_id, window_days, window_start, window_end, signal,"
        " data_sufficiency_pct, confidence)"
        " VALUES (%s, %s, %s, 7, now() - interval '7 days', now(), 'CGM', 90, 'OK')"
        " RETURNING id",
        (t.clinic_id, t.patient_id, profile_id),
    )
    ids = (
        profile_id,
        template_id,
        plan_id,
        item_id,
        draft_plan_id,
        snapshot_id,
        reminder_id,
        follow_up_id,
        outreach_id,
        note_id,
        review_id,
        validation_id,
        symptom_id,
        metrics_id,
    )
    assert all(isinstance(value, UUID) for value in ids)
    return CareRows(*ids)  # type: ignore[arg-type]
