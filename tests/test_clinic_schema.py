"""Remaining clinic tables: structure, RLS, care team rules and privileges."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    READONLY_ROLE,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    make_tenant,
    make_user,
    scalar,
)

CLINIC_TABLES = (
    "sites",
    "staff_sites",
    "care_team_assignments",
    "privacy_requests",
    "import_rows",
    "clinic_settings",
)
# Tables each role may read, filtered to the rows of its own clinic.
READERS = {
    "clinic.sites": ("CLINIC_ADMIN", "DOCTOR", "SYSTEM"),
    "clinic.staff_sites": ("CLINIC_ADMIN", "DOCTOR", "SYSTEM"),
    "clinic.import_rows": ("CLINIC_ADMIN",),
    "clinic.privacy_requests": ("CLINIC_ADMIN", "SYSTEM"),
    "clinic.clinic_settings": ("CLINIC_ADMIN", "DOCTOR", "PATIENT", "SYSTEM"),
}
DENIED = {
    "clinic.sites": ("PATIENT", "PLATFORM_ADMIN"),
    "clinic.staff_sites": ("PATIENT", "PLATFORM_ADMIN"),
    "clinic.import_rows": ("DOCTOR", "PATIENT", "SYSTEM", "PLATFORM_ADMIN"),
    "clinic.privacy_requests": ("DOCTOR", "PLATFORM_ADMIN"),
    "clinic.clinic_settings": ("PLATFORM_ADMIN",),
}


@pytest.fixture
def tenants(db: psycopg.Connection) -> tuple[Tenant, Tenant]:
    return make_tenant(db, "a"), make_tenant(db, "b")


def test_tables_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'clinic'"
    ).fetchall()
    assert {name for (name,) in rows} >= set(CLINIC_TABLES)


@pytest.mark.parametrize("table", sorted(READERS))
def test_readers_see_only_their_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    a, b = tenants
    for role in READERS[table]:
        act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
        assert count(db, table) == 1, role
        assert (
            scalar(
                db, f"SELECT count(*) FROM {table} WHERE clinic_id = %s", (b.clinic_id,)
            )
            == 0
        )
        act_as_superuser(db)


@pytest.mark.parametrize("table", sorted(DENIED))
def test_other_roles_see_nothing(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], table: str
) -> None:
    a, _ = tenants
    user = make_user(db, "denied@example.test")
    for role in DENIED[table]:
        act_as(db, clinic_id=a.clinic_id, user_id=user, role=role)
        assert count(db, table) == 0, role
        act_as_superuser(db)


def test_doctor_reads_only_own_assignments(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    other_doctor = make_user(db, "doctor-2@example.test")
    db.execute(
        "INSERT INTO clinic.staff_profiles (user_id, clinic_id, full_name)"
        " VALUES (%s, %s, 'Doctor 2')",
        (other_doctor, a.clinic_id),
    )
    db.execute(
        "INSERT INTO clinic.care_team_assignments (clinic_id, patient_id,"
        " doctor_user_id, assigned_by) VALUES (%s, %s, %s, %s)",
        (a.clinic_id, a.patient_id, other_doctor, a.admin_id),
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert scalar(db, "SELECT array_agg(id) FROM clinic.care_team_assignments") == [
        a.assignment_id
    ]
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "clinic.care_team_assignments") == 2  # noqa: PLR2004


def test_patient_reads_own_team_and_requests(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "clinic.care_team_assignments") == 1
    assert count(db, "clinic.privacy_requests") == 1
    db.execute(
        "INSERT INTO clinic.privacy_requests (clinic_id, patient_id, request_type,"
        " due_at) VALUES (%s, %s, 'ERASURE', now() + interval '1 day')",
        (a.clinic_id, a.patient_id),
    )
    assert count(db, "clinic.privacy_requests") == 2  # noqa: PLR2004


def test_patient_cannot_file_request_for_another_patient(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    for clinic_id, patient_id in (
        (a.clinic_id, b.patient_id),
        (b.clinic_id, b.patient_id),
    ):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(
                "INSERT INTO clinic.privacy_requests (clinic_id, patient_id,"
                " request_type, due_at)"
                " VALUES (%s, %s, 'ACCESS', now() + interval '1 day')",
                (clinic_id, patient_id),
            )
    # Only the clinic admin resolves requests.
    assert (
        db.execute("UPDATE clinic.privacy_requests SET status = 'COMPLETED'").rowcount
        == 0
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert (
        db.execute("UPDATE clinic.privacy_requests SET status = 'IN_PROGRESS'").rowcount
        == 1
    )


def test_clinic_admin_writes_only_its_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    db.execute(
        "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, 'Norte')",
        (a.clinic_id,),
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, 'Norte')",
            (b.clinic_id,),
        )
    assert (
        db.execute(
            "UPDATE clinic.sites SET is_active = false WHERE id = %s", (b.site_id,)
        ).rowcount
        == 0
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, 'Sur')",
            (a.clinic_id,),
        )


def test_site_names_are_unique_per_clinic_ignoring_case(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, 'SEDE A')",
            (a.clinic_id,),
        )
    db.execute(
        "INSERT INTO clinic.sites (clinic_id, name) VALUES (%s, 'SEDE A')",
        (b.clinic_id,),
    )


def test_global_settings_are_readable_but_not_without_context(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    db.execute(
        "INSERT INTO clinic.clinic_settings (recommendation_expiry_hours,"
        " forecast_refresh_minutes, dropout_inactivity_days, kpi_min_cell_size)"
        " VALUES (1, 1, 1, 1)"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "clinic.clinic_settings") == 2  # noqa: PLR2004
    act_as(db, clinic_id=None, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "clinic.clinic_settings") == 0
    act_as(db, clinic_id=a.clinic_id)
    assert count(db, "clinic.clinic_settings") == 0
    act_as(db, READONLY_ROLE)
    assert count(db, "clinic.clinic_settings") == 0


def test_only_one_global_and_one_clinic_settings_row(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    insert = (
        "INSERT INTO clinic.clinic_settings (clinic_id, recommendation_expiry_hours,"
        " forecast_refresh_minutes, dropout_inactivity_days, kpi_min_cell_size)"
        " VALUES (%s, 1, 1, 1, 1)"
    )
    db.execute(insert, (None,))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (None,))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id,))


def test_clinic_admin_cannot_write_the_global_settings(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    db.execute(
        "INSERT INTO clinic.clinic_settings (recommendation_expiry_hours,"
        " forecast_refresh_minutes, dropout_inactivity_days, kpi_min_cell_size)"
        " VALUES (1, 1, 1, 1)"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    updated = db.execute(
        "UPDATE clinic.clinic_settings SET kpi_min_cell_size = 9"
        " WHERE clinic_id IS NULL"
    ).rowcount
    assert updated == 0
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO clinic.clinic_settings (clinic_id,"
            " recommendation_expiry_hours, forecast_refresh_minutes,"
            " dropout_inactivity_days, kpi_min_cell_size)"
            " VALUES (NULL, 1, 1, 1, 1)"
        )
    updated = db.execute(
        "UPDATE clinic.clinic_settings SET kpi_min_cell_size = 9 WHERE clinic_id = %s",
        (a.clinic_id,),
    ).rowcount
    assert updated == 1


@pytest.mark.parametrize("column", ["kpi_min_cell_size", "dropout_inactivity_days"])
def test_settings_must_be_positive(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], column: str
) -> None:
    a, _ = tenants
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            f"UPDATE clinic.clinic_settings SET {column} = 0 WHERE clinic_id = %s",
            (a.clinic_id,),
        )


def test_one_primary_and_one_open_assignment_per_doctor(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    colleague = make_user(db, "colleague@example.test")
    db.execute(
        "INSERT INTO clinic.staff_profiles (user_id, clinic_id, full_name)"
        " VALUES (%s, %s, 'Colega')",
        (colleague, a.clinic_id),
    )
    insert = (
        "INSERT INTO clinic.care_team_assignments (clinic_id, patient_id,"
        " doctor_user_id, is_primary, assigned_by) VALUES (%s, %s, %s, %s, %s)"
    )
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, a.patient_id, colleague, True, a.admin_id))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, a.patient_id, a.doctor_id, False, a.admin_id))
    db.execute(insert, (a.clinic_id, a.patient_id, colleague, False, a.admin_id))


def test_primary_doctor_cannot_leave_the_team_without_replacement(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    colleague = make_user(db, "colleague@example.test")
    db.execute(
        "INSERT INTO clinic.staff_profiles (user_id, clinic_id, full_name)"
        " VALUES (%s, %s, 'Colega')",
        (colleague, a.clinic_id),
    )
    db.execute(
        "INSERT INTO clinic.care_team_assignments (clinic_id, patient_id,"
        " doctor_user_id, assigned_by) VALUES (%s, %s, %s, %s)",
        (a.clinic_id, a.patient_id, colleague, a.admin_id),
    )
    db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    end_primary = (
        "UPDATE clinic.care_team_assignments SET ended_at = now(), ended_by = %s,"
        " end_reason = 'Reasignacion del paciente' WHERE id = %s"
    )
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(end_primary, (a.admin_id, a.assignment_id))
    # Promoting the colleague in the same transaction satisfies the check.
    db.execute("SET CONSTRAINTS ALL DEFERRED")
    db.execute(end_primary, (a.admin_id, a.assignment_id))
    db.execute(
        "UPDATE clinic.care_team_assignments SET is_primary = true"
        " WHERE doctor_user_id = %s AND ended_at IS NULL",
        (colleague,),
    )
    db.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_ending_the_last_assignment_is_allowed(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    db.execute(
        "UPDATE clinic.care_team_assignments SET ended_at = now(), ended_by = %s,"
        " end_reason = 'Paciente dado de baja' WHERE id = %s",
        (a.admin_id, a.assignment_id),
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "ended_at = now()",
        "end_reason = 'Motivo suficientemente largo'",
        "ended_at = now(), end_reason = 'corto'",
        "ended_at = assigned_at - interval '1 day',"
        " end_reason = 'Motivo suficientemente largo'",
    ],
)
def test_assignment_end_rules(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], assignment: str
) -> None:
    a, _ = tenants
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            f"UPDATE clinic.care_team_assignments SET {assignment} WHERE id = %s",
            (a.assignment_id,),
        )


def test_import_rows_are_unique_per_batch_row_and_resource(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    batch_id = scalar(
        db,
        "SELECT batch_id FROM clinic.import_rows WHERE clinic_id = %s",
        (a.clinic_id,),
    )
    insert = (
        "INSERT INTO clinic.import_rows (clinic_id, batch_id, row_number,"
        " resource_type, status) VALUES (%s, %s, %s, %s, 'WARNING')"
    )
    # NULLS NOT DISTINCT: a CSV row (no resource type) cannot repeat.
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, batch_id, 1, None))
    db.execute(insert, (a.clinic_id, batch_id, 1, "Patient"))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, batch_id, 1, "Patient"))


@pytest.mark.parametrize(
    ("table", "privilege", "granted"),
    [
        ("clinic.sites", "DELETE", False),
        ("clinic.care_team_assignments", "DELETE", False),
        ("clinic.privacy_requests", "DELETE", False),
        ("clinic.clinic_settings", "DELETE", False),
        ("clinic.import_rows", "UPDATE", False),
        ("clinic.import_rows", "DELETE", False),
        ("clinic.staff_sites", "UPDATE", False),
        ("clinic.staff_sites", "DELETE", True),
        ("clinic.care_team_assignments", "UPDATE", True),
    ],
)
def test_app_privileges(
    db: psycopg.Connection, table: str, privilege: str, granted: bool
) -> None:
    assert (
        scalar(
            db, "SELECT has_table_privilege(%s, %s, %s)", (APP_ROLE, table, privilege)
        )
        is granted
    )


def test_readonly_cannot_read_privacy_request_text(db: psycopg.Connection) -> None:
    act_as(db, READONLY_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("SELECT description FROM clinic.privacy_requests")
    db.execute("SELECT request_type FROM clinic.privacy_requests")
