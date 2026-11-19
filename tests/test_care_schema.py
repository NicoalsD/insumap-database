"""Care schema: structure, RLS, immutable versions, plan guards and append-only."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    CareRows,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    make_profile,
    make_tenant,
    make_user,
    populate_care,
    scalar,
)

CARE_TABLES = (
    "target_profiles",
    "patient_target_assignments",
    "protocol_templates",
    "treatment_plans",
    "treatment_plan_items",
    "treatment_plan_snapshots",
    "reminder_schedules",
    "follow_ups",
    "clinical_notes",
    "triage_reviews",
    "reading_validation_settings",
    "symptom_catalog",
    "glycemic_metric_snapshots",
)
CATALOGS = (
    "target_profiles",
    "protocol_templates",
    "reading_validation_settings",
    "symptom_catalog",
)
# Tables with data of the patient: no access for the clinic administrator.
CLINICAL = tuple(table for table in CARE_TABLES if table not in CATALOGS)
APPEND_ONLY = {
    "treatment_plan_snapshots": "snapshot_id",
    "clinical_notes": "note_id",
    "triage_reviews": "review_id",
    "glycemic_metric_snapshots": "metrics_id",
}
VERSIONED = {
    "target_profiles": "profile_id",
    "protocol_templates": "template_id",
    "reading_validation_settings": "validation_id",
}
CLINICAL_ROLES = ("DOCTOR", "SYSTEM")
EXPECTED_ROWS_PER_TENANT = {
    "target_profiles": 1,
    "protocol_templates": 1,
    "reading_validation_settings": 1,
    "symptom_catalog": 1,
    "patient_target_assignments": 1,
    "treatment_plans": 2,
    "treatment_plan_items": 1,
    "treatment_plan_snapshots": 1,
    "reminder_schedules": 1,
    "follow_ups": 2,
    "clinical_notes": 1,
    "triage_reviews": 1,
    "glycemic_metric_snapshots": 1,
}


@pytest.fixture
def world(
    db: psycopg.Connection,
) -> tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]]:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    return (a, populate_care(db, a)), (b, populate_care(db, b))


def test_tables_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'care'"
    ).fetchall()
    assert {name for (name,) in rows} == set(CARE_TABLES)


def test_no_clinical_number_is_a_default(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'care' AND column_default IS NOT NULL"
        "   AND (column_name LIKE '%\\_mg\\_dl' OR column_name LIKE '%\\_pct')"
    ).fetchall()
    assert rows == []


@pytest.mark.parametrize("table", CARE_TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
) -> None:
    assert count(db, f"care.{table}") > 0
    act_as(db)
    assert count(db, f"care.{table}") == 0


@pytest.mark.parametrize("table", CARE_TABLES)
def test_staff_see_only_their_clinic(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
) -> None:
    (a, _), _ = world
    for role in CLINICAL_ROLES:
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        assert count(db, f"care.{table}") == EXPECTED_ROWS_PER_TENANT[table], role
        act_as_superuser(db)


@pytest.mark.parametrize("table", CLINICAL)
@pytest.mark.parametrize("role", ["PLATFORM_ADMIN", "CLINIC_ADMIN"])
def test_administrators_have_no_clinical_access(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
    role: str,
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    visible = count(db, f"care.{table}")
    # The only exception: outreach tasks, without clinical detail.
    assert visible == (1 if (table, role) == ("follow_ups", "CLINIC_ADMIN") else 0)


@pytest.mark.parametrize("table", CATALOGS)
def test_catalog_rows_of_another_clinic_are_hidden(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
) -> None:
    (a, _), (b, _) = world
    for role in ("CLINIC_ADMIN", "DOCTOR", "SYSTEM"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
        assert (
            scalar(
                db,
                f"SELECT count(*) FROM care.{table} WHERE clinic_id = %s",
                (b.clinic_id,),
            )
            == 0
        )
        act_as_superuser(db)


def test_global_seed_rows_are_visible_to_every_clinic_role(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), _ = world
    make_profile(db, None, "GLOBAL_PROFILE", status="ACTIVE")
    for role in ("CLINIC_ADMIN", "DOCTOR", "PATIENT", "SYSTEM"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
        assert count(db, "care.target_profiles") == 2, role  # noqa: PLR2004
        act_as_superuser(db)
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="PLATFORM_ADMIN")
    assert count(db, "care.target_profiles") == 0
    act_as(db, clinic_id=None, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "care.target_profiles") == 0


def test_templates_are_hidden_from_patients(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "care.protocol_templates") == 0
    assert count(db, "care.target_profiles") == 1
    assert count(db, "care.symptom_catalog") == 1


def test_clinic_admin_writes_only_its_own_catalog_rows(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), (b, _) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    insert = (
        "INSERT INTO care.symptom_catalog (clinic_id, code, label_es)"
        " VALUES (%s, %s, 'Nuevo')"
    )
    db.execute(insert, (a.clinic_id, "OWN_SYMPTOM"))
    for clinic_id in (b.clinic_id, None):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, (clinic_id, "OTHER_SYMPTOM"))
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id, "DOCTOR_SYMPTOM"))


@pytest.mark.parametrize(("table", "row"), sorted(VERSIONED.items()))
def test_active_versions_are_immutable(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
    row: str,
) -> None:
    (_, rows), _ = world
    row_id: UUID = getattr(rows, row)
    column = {
        "target_profiles": "label = 'Otro'",
        "protocol_templates": "name = 'Otro'",
        "reading_validation_settings": "max_measurable_mg_dl = 999",
    }[table]
    with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
        db.execute(f"UPDATE care.{table} SET {column} WHERE id = %s", (row_id,))
    with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
        db.execute(f"UPDATE care.{table} SET status = 'DRAFT' WHERE id = %s", (row_id,))
    with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
        db.execute(
            f"UPDATE care.{table} SET status = 'RETIRED', {column} WHERE id = %s",
            (row_id,),
        )
    retired = db.execute(
        f"UPDATE care.{table} SET status = 'RETIRED' WHERE id = %s", (row_id,)
    ).rowcount
    assert retired == 1


def test_draft_versions_can_be_edited_and_activated(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), _ = world
    draft = make_profile(db, a.clinic_id, "DRAFT_PROFILE")
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    updated = db.execute(
        "UPDATE care.target_profiles SET label = 'Editado', status = 'ACTIVE',"
        " activated_at = now() WHERE id = %s",
        (draft,),
    ).rowcount
    assert updated == 1


def test_one_active_version_per_code_and_clinic(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), _ = world
    with pytest.raises(errors.UniqueViolation), db.transaction():
        make_profile(db, a.clinic_id, "CLINIC_PROFILE", version=2, status="ACTIVE")
    make_profile(db, a.clinic_id, "CLINIC_PROFILE", version=2)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        make_profile(db, a.clinic_id, "CLINIC_PROFILE", version=2)
    # NULLS NOT DISTINCT: a global (code, version) pair cannot repeat either.
    make_profile(db, None, "GLOBAL", status="ACTIVE")
    with pytest.raises(errors.UniqueViolation), db.transaction():
        make_profile(db, None, "GLOBAL", status="DRAFT")


@pytest.mark.parametrize(
    "change",
    [
        "range_high_mg_dl = range_low_mg_dl",
        "very_low_mg_dl = range_low_mg_dl",
        "very_high_mg_dl = range_high_mg_dl",
        "tir_min_pct = 101",
        "min_days = 0",
        "fasting_low_mg_dl = 90, fasting_high_mg_dl = 80",
        "code = 'lower'",
        "population = 'KIDS'",
        "change_reason = 'corto'",
    ],
)
def test_target_profile_checks(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    change: str,
) -> None:
    (a, _), _ = world
    draft = make_profile(db, a.clinic_id, "DRAFT_PROFILE")
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(f"UPDATE care.target_profiles SET {change} WHERE id = %s", (draft,))


def test_plan_items_change_only_in_draft_plans(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    insert = (
        "INSERT INTO care.treatment_plan_items (clinic_id, plan_id, item_type, name)"
        " VALUES (%s, %s, 'MEASURE', 'Otra')"
    )
    db.execute(insert, (a.clinic_id, rows.draft_plan_id))
    with pytest.raises(errors.RaiseException, match="not DRAFT"), db.transaction():
        db.execute(insert, (a.clinic_id, rows.plan_id))
    with pytest.raises(errors.RaiseException, match="not DRAFT"), db.transaction():
        db.execute(
            "UPDATE care.treatment_plan_items SET plan_id = %s WHERE id = %s",
            (rows.plan_id, rows.item_id),
        )
    deleted = db.execute(
        "DELETE FROM care.treatment_plan_items WHERE id = %s", (rows.item_id,)
    ).rowcount
    assert deleted == 1


def test_items_of_an_active_plan_cannot_be_deleted(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    # Place an item on the ACTIVE plan as if it had been added while DRAFT.
    db.execute("SET LOCAL session_replication_role = replica")
    db.execute(
        "INSERT INTO care.treatment_plan_items (clinic_id, plan_id, item_type, name)"
        " VALUES (%s, %s, 'MEASURE', 'Vigente')",
        (a.clinic_id, rows.plan_id),
    )
    db.execute("SET LOCAL session_replication_role = origin")
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.RaiseException, match="not DRAFT"), db.transaction():
        db.execute(
            "DELETE FROM care.treatment_plan_items WHERE plan_id = %s",
            (rows.plan_id,),
        )
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "care.treatment_plan_items") == 1


def test_active_plan_only_becomes_superseded(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    for change in ("notes = 'Cambio'", "status = 'DISCARDED'", "status = 'DRAFT'"):
        with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
            db.execute(
                f"UPDATE care.treatment_plans SET {change} WHERE id = %s",
                (rows.plan_id,),
            )
    with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
        db.execute(
            "UPDATE care.treatment_plans SET status = 'SUPERSEDED',"
            " notes = 'Cambio' WHERE id = %s",
            (rows.plan_id,),
        )
    updated = db.execute(
        "UPDATE care.treatment_plans SET status = 'SUPERSEDED',"
        " superseded_at = now(), updated_at = now(), lock_version = lock_version + 1"
        " WHERE id = %s",
        (rows.plan_id,),
    ).rowcount
    assert updated == 1
    # The next version can now be activated.
    activated = db.execute(
        "UPDATE care.treatment_plans SET status = 'ACTIVE', activated_by = %s,"
        " activated_at = now() WHERE id = %s",
        (a.doctor_id, rows.draft_plan_id),
    ).rowcount
    assert activated == 1


def test_one_active_plan_and_unique_versions_per_patient(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "UPDATE care.treatment_plans SET status = 'ACTIVE' WHERE id = %s",
            (rows.draft_plan_id,),
        )
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO care.treatment_plans (clinic_id, patient_id, version,"
            " target_profile_id, created_by) VALUES (%s, %s, 2, %s, %s)",
            (a.clinic_id, a.patient_id, rows.profile_id, a.doctor_id),
        )


def test_patient_sees_only_the_active_plan_and_its_items(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, "SELECT array_agg(id) FROM care.treatment_plans") == [
        rows.plan_id
    ]
    # The only item belongs to the DRAFT plan, which the patient cannot see.
    assert count(db, "care.treatment_plan_items") == 0
    assert count(db, "care.reminder_schedules") == 1
    assert count(db, "care.glycemic_metric_snapshots") == 1
    assert count(db, "care.patient_target_assignments") == 1
    assert scalar(db, "SELECT array_agg(id) FROM care.follow_ups") == [
        rows.follow_up_id
    ]
    for table in (
        "clinical_notes",
        "triage_reviews",
        "treatment_plan_snapshots",
    ):
        assert count(db, f"care.{table}") == 0


def test_patient_only_adjusts_its_own_reminders(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), (b, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    update = "UPDATE care.reminder_schedules SET is_active = false WHERE id = %s"
    assert db.execute(update, (rows.reminder_id,)).rowcount == 1
    assert db.execute(update, (other_rows.reminder_id,)).rowcount == 0
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO care.reminder_schedules (clinic_id, patient_id,"
            " reminder_type, local_time, message_key, set_by)"
            " VALUES (%s, %s, 'CONTROL', '09:00', 'reminder.control', %s)",
            (a.clinic_id, a.patient_id, a.patient_user_id),
        )
    # A patient cannot hand its reminder to another patient or clinic.
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "UPDATE care.reminder_schedules SET patient_id = %s WHERE id = %s",
            (b.patient_id, rows.reminder_id),
        )


def test_patient_cannot_write_clinical_tables(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    statements = (
        (
            "INSERT INTO care.clinical_notes (clinic_id, patient_id, author_user_id,"
            " body) VALUES (%s, %s, %s, 'x')",
            (a.clinic_id, a.patient_id, a.patient_user_id),
        ),
        (
            "INSERT INTO care.follow_ups (clinic_id, patient_id, kind, status,"
            " title, created_by) VALUES (%s, %s, 'CONTROL', 'SCHEDULED', 'x', %s)",
            (a.clinic_id, a.patient_id, a.patient_user_id),
        ),
        (
            "INSERT INTO care.treatment_plans (clinic_id, patient_id, version,"
            " target_profile_id, created_by) VALUES (%s, %s, 9, %s, %s)",
            (a.clinic_id, a.patient_id, rows.profile_id, a.patient_user_id),
        ),
    )
    for sql, params in statements:
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(sql, params)


def test_doctor_cannot_write_another_clinic(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), (b, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO care.clinical_notes (clinic_id, patient_id, author_user_id,"
            " body) VALUES (%s, %s, %s, 'x')",
            (b.clinic_id, b.patient_id, a.doctor_id),
        )
    updated = db.execute(
        "UPDATE care.treatment_plans SET lock_version = lock_version + 1 WHERE id = %s",
        (other_rows.draft_plan_id,),
    ).rowcount
    assert updated == 0


def test_clinic_admin_manages_only_outreach_tasks(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert scalar(db, "SELECT array_agg(id) FROM care.follow_ups") == [rows.outreach_id]
    assert (
        db.execute(
            "UPDATE care.follow_ups SET status = 'DONE' WHERE id = %s",
            (rows.follow_up_id,),
        ).rowcount
        == 0
    )
    assert (
        db.execute(
            "UPDATE care.follow_ups SET status = 'DISMISSED' WHERE id = %s",
            (rows.outreach_id,),
        ).rowcount
        == 1
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO care.follow_ups (clinic_id, patient_id, kind, status,"
            " title, created_by)"
            " VALUES (%s, %s, 'CONTROL', 'SCHEDULED', 'x', %s)",
            (a.clinic_id, a.patient_id, a.admin_id),
        )


@pytest.mark.parametrize(
    ("kind", "status"),
    [("CONTROL", "OPEN"), ("CONTROL", "DISMISSED"), ("OUTREACH", "SCHEDULED")],
)
def test_follow_up_status_matches_its_kind(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    kind: str,
    status: str,
) -> None:
    (a, _), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO care.follow_ups (clinic_id, patient_id, kind, status,"
            " title, created_by) VALUES (%s, %s, %s, %s, 'x', %s)",
            (a.clinic_id, a.patient_id, kind, status, a.doctor_id),
        )


def test_only_the_doctor_assigns_goal_profiles(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    insert = (
        "INSERT INTO care.patient_target_assignments (clinic_id, patient_id,"
        " target_profile_id, assigned_by) VALUES (%s, %s, %s, %s)"
    )
    params = (a.clinic_id, a.patient_id, rows.profile_id, a.doctor_id)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    # One open assignment per patient: close the current one first.
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, params)
    db.execute(
        "UPDATE care.patient_target_assignments SET ended_at = now()"
        " WHERE patient_id = %s",
        (a.patient_id,),
    )
    db.execute(insert, params)
    for role in ("SYSTEM", "CLINIC_ADMIN", "PATIENT"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, params)


@pytest.mark.parametrize(("table", "row"), sorted(APPEND_ONLY.items()))
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_append_only_tables_reject_mutation(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
    table: str,
    row: str,
    operation: str,
) -> None:
    (_, rows), _ = world
    row_id = getattr(rows, row)
    statement = (
        f"UPDATE care.{table} SET clinic_id = clinic_id WHERE id = %s"
        if operation == "UPDATE"
        else f"DELETE FROM care.{table} WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (row_id,))


def test_snapshot_numbers_are_unique_per_plan(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    insert = (
        "INSERT INTO care.treatment_plan_snapshots (clinic_id, plan_id,"
        " snapshot_no, snapshot, taken_by) VALUES (%s, %s, %s, %s, %s)"
    )
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (a.clinic_id, rows.plan_id, 1, "{}", a.doctor_id))
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (a.clinic_id, rows.plan_id, 2, "[]", a.doctor_id))
    db.execute(insert, (a.clinic_id, rows.plan_id, 2, "{}", a.doctor_id))


def test_clinical_note_corrections_are_new_rows(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(
        "INSERT INTO care.clinical_notes (clinic_id, patient_id, author_user_id,"
        " body, supersedes_id) VALUES (%s, %s, %s, 'Correccion', %s)",
        (a.clinic_id, a.patient_id, a.doctor_id, rows.note_id),
    )
    assert count(db, "care.clinical_notes") == 2  # noqa: PLR2004
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO care.clinical_notes (clinic_id, patient_id, author_user_id,"
            " body) VALUES (%s, %s, %s, '')",
            (a.clinic_id, a.patient_id, a.doctor_id),
        )


@pytest.mark.parametrize("table", ["clinical_notes", "treatment_plan_snapshots"])
def test_app_cannot_update_or_delete_append_only_tables(
    db: psycopg.Connection, table: str
) -> None:
    for privilege in ("UPDATE", "DELETE"):
        assert (
            scalar(
                db,
                "SELECT has_table_privilege(%s, %s, %s)",
                (APP_ROLE, f"care.{table}", privilege),
            )
            is False
        )


def test_app_deletes_only_plan_items(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.role_table_grants"
        " WHERE grantee = %s AND table_schema = 'care'"
        "   AND privilege_type = 'DELETE'",
        (APP_ROLE,),
    ).fetchall()
    assert rows == [("treatment_plan_items",)]


def test_other_users_do_not_become_patients_of_a_clinic(
    db: psycopg.Connection,
    world: tuple[tuple[Tenant, CareRows], tuple[Tenant, CareRows]],
) -> None:
    (a, _), _ = world
    stranger = make_user(db, "stranger@example.test")
    act_as(db, clinic_id=a.clinic_id, user_id=stranger, role="PATIENT")
    for table in ("treatment_plans", "reminder_schedules", "follow_ups"):
        assert count(db, f"care.{table}") == 0
