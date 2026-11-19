"""Monitoring schema: append-only records, corrections, RLS and the view."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    RECORD_INSERTS,
    MonitoringRows,
    Tenant,
    act_as,
    act_as_superuser,
    add_membership,
    count,
    make_patient,
    make_tenant,
    make_user,
    populate_monitoring,
    scalar,
)

TABLES = (
    "sync_batches",
    "glucose_readings",
    "reading_issues",
    "insulin_doses",
    "meals",
    "physical_activities",
    "symptoms",
    "lab_results",
)
RECORD_TABLES = (
    "glucose_readings",
    "insulin_doses",
    "meals",
    "physical_activities",
    "symptoms",
    "lab_results",
)
ROW_FIELD = {
    "sync_batches": "batch_id",
    "glucose_readings": "reading_id",
    "reading_issues": "issue_id",
    "insulin_doses": "insulin_id",
    "meals": "meal_id",
    "physical_activities": "activity_id",
    "symptoms": "symptom_record_id",
    "lab_results": "lab_id",
}
World = tuple[tuple[Tenant, MonitoringRows], tuple[Tenant, MonitoringRows]]


@pytest.fixture
def world(db: psycopg.Connection) -> World:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    return (a, populate_monitoring(db, a)), (b, populate_monitoring(db, b))


def test_tables_and_view_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name, table_type FROM information_schema.tables"
        " WHERE table_schema = 'monitoring'"
    ).fetchall()
    assert {name for name, kind in rows if kind == "BASE TABLE"} == set(TABLES)
    assert ("current_glucose_readings", "VIEW") in rows


@pytest.mark.parametrize("table", TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection, world: World, table: str
) -> None:
    assert count(db, f"monitoring.{table}") > 0
    act_as(db)
    assert count(db, f"monitoring.{table}") == 0


@pytest.mark.parametrize("table", TABLES)
def test_patient_and_staff_see_only_their_rows(
    db: psycopg.Connection, world: World, table: str
) -> None:
    (a, rows), _ = world
    for role, user in (
        ("PATIENT", a.patient_user_id),
        ("DOCTOR", a.doctor_id),
        ("SYSTEM", a.doctor_id),
    ):
        act_as(db, clinic_id=a.clinic_id, user_id=user, role=role)
        assert scalar(db, f"SELECT array_agg(id) FROM monitoring.{table}") == [
            getattr(rows, ROW_FIELD[table])
        ], role
        assert count(db, f"monitoring.{table}") == 1
        act_as_superuser(db)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "PLATFORM_ADMIN"])
def test_administrators_see_no_clinical_records(
    db: psycopg.Connection, world: World, table: str, role: str
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    assert count(db, f"monitoring.{table}") == 0


def test_another_patient_of_the_same_clinic_sees_nothing(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), _ = world
    user = make_user(db, "second-patient@example.test")
    add_membership(db, user, a.clinic_id, "PATIENT")
    make_patient(db, a.clinic_id, user, "MRN-second")
    act_as(db, clinic_id=a.clinic_id, user_id=user, role="PATIENT")
    for table in TABLES:
        assert count(db, f"monitoring.{table}") == 0, table


@pytest.mark.parametrize("table", sorted(RECORD_INSERTS))
def test_patient_inserts_only_own_records(
    db: psycopg.Connection, world: World, table: str
) -> None:
    (a, _), (b, _) = world
    sql = RECORD_INSERTS[table]
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, sql, (a.clinic_id, a.patient_id, a.patient_user_id))
    cases = (
        (a.clinic_id, b.patient_id, a.patient_user_id),  # other patient
        (b.clinic_id, b.patient_id, a.patient_user_id),  # other clinic
        (a.clinic_id, a.patient_id, a.doctor_id),  # recorded_by someone else
    )
    for params in cases:
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(sql, params)


@pytest.mark.parametrize("table", sorted(RECORD_INSERTS))
@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "SYSTEM", "PLATFORM_ADMIN"])
def test_other_roles_cannot_insert_records(
    db: psycopg.Connection, world: World, table: str, role: str
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(RECORD_INSERTS[table], (a.clinic_id, a.patient_id, a.admin_id))


def test_only_lab_results_accept_a_doctor(db: psycopg.Connection, world: World) -> None:
    (a, _), (b, _) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    for table, sql in RECORD_INSERTS.items():
        params = (a.clinic_id, a.patient_id, a.doctor_id)
        if table == "lab_results":
            assert scalar(db, sql, params)
            with pytest.raises(errors.InsufficientPrivilege), db.transaction():
                db.execute(sql, (b.clinic_id, b.patient_id, a.doctor_id))
        else:
            with pytest.raises(errors.InsufficientPrivilege), db.transaction():
                db.execute(sql, params)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_records_are_append_only_even_for_the_superuser(
    db: psycopg.Connection, world: World, table: str, operation: str
) -> None:
    (_, rows), _ = world
    statement = (
        f"UPDATE monitoring.{table} SET clinic_id = clinic_id WHERE id = %s"
        if operation == "UPDATE"
        else f"DELETE FROM monitoring.{table} WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (getattr(rows, ROW_FIELD[table]),))


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE", "TRUNCATE"])
def test_app_has_no_update_or_delete(
    db: psycopg.Connection, table: str, privilege: str
) -> None:
    assert (
        scalar(
            db,
            "SELECT has_table_privilege(%s, %s, %s)",
            (APP_ROLE, f"monitoring.{table}", privilege),
        )
        is False
    )


def test_app_cannot_update_as_a_patient(db: psycopg.Connection, world: World) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("UPDATE monitoring.glucose_readings SET value_mg_dl = 1")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("DELETE FROM monitoring.meals")


def test_corrections_point_to_the_original(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    correction = scalar(
        db,
        "INSERT INTO monitoring.glucose_readings (clinic_id, patient_id,"
        " recorded_by, client_reading_id, measured_at, value_mg_dl, source,"
        " entry_type, supersedes_id, correction_reason)"
        " VALUES (%s, %s, %s, uuidv7(), now(), 140, 'MANUAL', 'CORRECTION', %s,"
        " 'Registrada por error')"
        " RETURNING id",
        (a.clinic_id, a.patient_id, a.patient_user_id, rows.reading_id),
    )
    current = scalar(
        db, "SELECT array_agg(id) FROM monitoring.current_glucose_readings"
    )
    assert current == [correction]
    assert count(db, "monitoring.glucose_readings") == 2  # noqa: PLR2004
    # Voiding the correction leaves no current reading.
    db.execute(
        "INSERT INTO monitoring.glucose_readings (clinic_id, patient_id,"
        " recorded_by, client_reading_id, measured_at, value_mg_dl, source,"
        " entry_type, supersedes_id, correction_reason)"
        " VALUES (%s, %s, %s, uuidv7(), now(), 140, 'MANUAL', 'VOID', %s,"
        " 'Anulada por el paciente')",
        (a.clinic_id, a.patient_id, a.patient_user_id, correction),
    )
    assert count(db, "monitoring.current_glucose_readings") == 0


@pytest.mark.parametrize(
    "columns",
    [
        ("'ORIGINAL'", "%s", "NULL"),
        ("'ORIGINAL'", "NULL", "'Motivo suficiente'"),
        ("'CORRECTION'", "NULL", "'Motivo suficiente'"),
        ("'CORRECTION'", "%s", "NULL"),
        ("'VOID'", "%s", "'corto'"),
        ("'EDIT'", "%s", "'Motivo suficiente'"),
    ],
)
def test_correction_rules(
    db: psycopg.Connection, world: World, columns: tuple[str, str, str]
) -> None:
    (a, rows), _ = world
    entry_type, supersedes, reason = columns
    params: tuple[object, ...] = (a.clinic_id, a.patient_id, a.patient_user_id)
    if supersedes == "%s":
        params += (rows.reading_id,)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO monitoring.glucose_readings (clinic_id, patient_id,"
            " recorded_by, client_reading_id, measured_at, value_mg_dl, source,"
            f" entry_type, supersedes_id, correction_reason) VALUES (%s, %s, %s,"
            f" uuidv7(), now(), 100, 'MANUAL', {entry_type}, {supersedes}, {reason})",
            params,
        )


def test_current_view_applies_the_row_level_security_of_the_caller(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(
        db, "SELECT array_agg(id) FROM monitoring.current_glucose_readings"
    ) == [rows.reading_id]
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "monitoring.current_glucose_readings") == 0
    act_as(db)
    assert count(db, "monitoring.current_glucose_readings") == 0
    assert other_rows.reading_id != rows.reading_id


@pytest.mark.parametrize("table", sorted(RECORD_INSERTS))
def test_client_ids_make_inserts_idempotent(
    db: psycopg.Connection, world: World, table: str
) -> None:
    (a, _), _ = world
    column = "client_reading_id" if table == "glucose_readings" else "client_record_id"
    client_id = scalar(
        db,
        f"SELECT {column} FROM monitoring.{table} WHERE patient_id = %s",
        (a.patient_id,),
    )
    assert isinstance(client_id, UUID)
    sql = RECORD_INSERTS[table].replace("uuidv7()", "%s", 1)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(sql, (a.clinic_id, a.patient_id, a.patient_user_id, client_id))


def test_batch_counts_must_add_up(db: psycopg.Connection, world: World) -> None:
    (a, _), _ = world
    insert = (
        "INSERT INTO monitoring.sync_batches (clinic_id, patient_id, source,"
        " submitted_by, total_items, created_count, duplicate_count,"
        " rejected_count) VALUES (%s, %s, 'CGM_CSV', %s, %s, 1, 1, 1)"
    )
    params = (a.clinic_id, a.patient_id, a.patient_user_id)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (*params, 2))
    db.execute(insert, (*params, 3))


def test_records_can_reference_a_batch_inserted_later(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), _ = world
    batch_id = scalar(db, "SELECT uuidv7()")
    db.execute(
        "INSERT INTO monitoring.meals (clinic_id, patient_id, recorded_by,"
        " client_record_id, eaten_at, meal_type, carbs_g, sync_batch_id)"
        " VALUES (%s, %s, %s, uuidv7(), now(), 'SNACK', 10, %s)",
        (a.clinic_id, a.patient_id, a.patient_user_id, batch_id),
    )
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    db.execute(
        "INSERT INTO monitoring.sync_batches (id, clinic_id, patient_id, source,"
        " submitted_by, total_items, created_count, duplicate_count, rejected_count)"
        " VALUES (%s, %s, %s, 'SIMULATOR', %s, 1, 1, 0, 0)",
        (batch_id, a.clinic_id, a.patient_id, a.patient_user_id),
    )
    db.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_reading_issues_follow_the_visibility_of_their_reading(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    insert = (
        "INSERT INTO monitoring.reading_issues (clinic_id, reading_id, issue_code)"
        " VALUES (%s, %s, %s)"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    db.execute(insert, (a.clinic_id, rows.reading_id, "UNIT_MISMATCH_SUSPECTED"))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, rows.reading_id, "UNIT_MISMATCH_SUSPECTED"))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id, other_rows.reading_id, "DUPLICATE_SUSPECTED"))
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (a.clinic_id, rows.reading_id, "OTHER"))
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert count(db, "monitoring.reading_issues") == 2  # noqa: PLR2004
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id, rows.reading_id, "DUPLICATE_SUSPECTED"))


@pytest.mark.parametrize(
    ("table", "valid", "invalid"),
    [
        ("glucose_readings", "100, 'MANUAL'", "0, 'MANUAL'"),
        ("glucose_readings", "100, 'MANUAL'", "100, 'WATCH'"),
        ("insulin_doses", "'BASAL'", "'NPH'"),
        ("insulin_doses", "'Producto', 5", "'Producto', 0"),
        ("meals", "'LUNCH', 40", "'LUNCH', -1"),
        ("meals", "'LUNCH'", "'BRUNCH'"),
        ("physical_activities", "30, 'LOW'", "0, 'LOW'"),
        ("physical_activities", "'LOW'", "'EXTREME'"),
        ("lab_results", "'%%'", "'g/dL'"),
        ("lab_results", "'PATIENT_REPORTED'", "'GUESS'"),
        ("lab_results", "7, '%%'", "0, '%%'"),
    ],
)
def test_value_checks(
    db: psycopg.Connection, world: World, table: str, valid: str, invalid: str
) -> None:
    (a, _), _ = world
    sql = RECORD_INSERTS[table]
    assert valid in sql
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            sql.replace(valid, invalid),
            (a.clinic_id, a.patient_id, a.patient_user_id),
        )
