"""Messaging schema: one thread per patient, append-only messages and RLS."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    MessagingRows,
    Tenant,
    act_as,
    act_as_superuser,
    add_membership,
    count,
    make_patient,
    make_tenant,
    make_user,
    populate_messaging,
    scalar,
)

TABLES = ("threads", "messages", "thread_reads")
World = tuple[tuple[Tenant, MessagingRows], tuple[Tenant, MessagingRows]]
INSERT_MESSAGE = (
    "INSERT INTO messaging.messages (clinic_id, thread_id, sender_user_id,"
    " sender_role, body) VALUES (%s, %s, %s, %s, %s)"
)


@pytest.fixture
def world(db: psycopg.Connection) -> World:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    return (a, populate_messaging(db, a)), (b, populate_messaging(db, b))


def test_tables_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'messaging'"
    ).fetchall()
    assert {name for (name,) in rows} == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection, world: World, table: str
) -> None:
    assert count(db, f"messaging.{table}") > 0
    act_as(db)
    assert count(db, f"messaging.{table}") == 0


@pytest.mark.parametrize("table", ["threads", "messages"])
def test_staff_and_patient_see_only_their_clinic(
    db: psycopg.Connection, world: World, table: str
) -> None:
    (a, _), (b, _) = world
    for role, user in (
        ("DOCTOR", a.doctor_id),
        ("SYSTEM", a.doctor_id),
        ("PATIENT", a.patient_user_id),
    ):
        act_as(db, clinic_id=a.clinic_id, user_id=user, role=role)
        assert count(db, f"messaging.{table}") == 1, role
        assert (
            scalar(
                db,
                f"SELECT count(*) FROM messaging.{table} WHERE clinic_id = %s",
                (b.clinic_id,),
            )
            == 0
        )
        act_as_superuser(db)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "PLATFORM_ADMIN"])
def test_administrators_have_no_access(
    db: psycopg.Connection, world: World, table: str, role: str
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    assert count(db, f"messaging.{table}") == 0


def test_a_patient_does_not_see_the_thread_of_another_patient(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), _ = world
    user = make_user(db, "second@example.test")
    add_membership(db, user, a.clinic_id, "PATIENT")
    make_patient(db, a.clinic_id, user, "MRN-second")
    act_as(db, clinic_id=a.clinic_id, user_id=user, role="PATIENT")
    assert count(db, "messaging.threads") == 0
    assert count(db, "messaging.messages") == 0


def test_patient_writes_in_its_own_thread_as_a_patient(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (b, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    db.execute(
        INSERT_MESSAGE,
        (a.clinic_id, rows.thread_id, a.patient_user_id, "PATIENT", "Otra"),
    )
    cases = (
        (a.clinic_id, rows.thread_id, a.patient_user_id, "DOCTOR"),
        (a.clinic_id, rows.thread_id, a.doctor_id, "PATIENT"),
        (b.clinic_id, other_rows.thread_id, a.patient_user_id, "PATIENT"),
        (a.clinic_id, other_rows.thread_id, a.patient_user_id, "PATIENT"),
    )
    for clinic_id, thread_id, sender, role in cases:
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(INSERT_MESSAGE, (clinic_id, thread_id, sender, role, "x"))


def test_patient_cannot_write_in_the_thread_of_another_patient(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    user = make_user(db, "second@example.test")
    add_membership(db, user, a.clinic_id, "PATIENT")
    make_patient(db, a.clinic_id, user, "MRN-second")
    act_as(db, clinic_id=a.clinic_id, user_id=user, role="PATIENT")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            INSERT_MESSAGE, (a.clinic_id, rows.thread_id, user, "PATIENT", "intruso")
        )


def test_doctor_replies_but_other_roles_do_not(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (b, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(
        INSERT_MESSAGE,
        (a.clinic_id, rows.thread_id, a.doctor_id, "DOCTOR", "Respuesta"),
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            INSERT_MESSAGE,
            (a.clinic_id, rows.thread_id, a.doctor_id, "PATIENT", "Suplantacion"),
        )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            INSERT_MESSAGE,
            (b.clinic_id, other_rows.thread_id, a.doctor_id, "DOCTOR", "Ajeno"),
        )
    for role in ("SYSTEM", "CLINIC_ADMIN", "PLATFORM_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(
                INSERT_MESSAGE,
                (a.clinic_id, rows.thread_id, a.doctor_id, "DOCTOR", "No"),
            )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_messages_are_append_only(
    db: psycopg.Connection, world: World, operation: str
) -> None:
    (_, rows), _ = world
    statement = (
        "UPDATE messaging.messages SET body = 'x' WHERE id = %s"
        if operation == "UPDATE"
        else "DELETE FROM messaging.messages WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (rows.message_id,))


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE"])
def test_app_cannot_change_messages(db: psycopg.Connection, privilege: str) -> None:
    assert (
        scalar(
            db,
            "SELECT has_table_privilege(%s, 'messaging.messages', %s)",
            (APP_ROLE, privilege),
        )
        is False
    )


def test_message_body_cannot_be_empty(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            INSERT_MESSAGE,
            (a.clinic_id, rows.thread_id, a.patient_user_id, "PATIENT", ""),
        )


def test_one_thread_per_patient(db: psycopg.Connection, world: World) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO messaging.threads (clinic_id, patient_id) VALUES (%s, %s)",
            (a.clinic_id, a.patient_id),
        )


def test_new_thread_is_created_by_the_patient_or_the_doctor_only(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), (b, _) = world
    user = make_user(db, "second@example.test")
    add_membership(db, user, a.clinic_id, "PATIENT")
    patient_id = make_patient(db, a.clinic_id, user, "MRN-second")
    insert = "INSERT INTO messaging.threads (clinic_id, patient_id) VALUES (%s, %s)"
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id, patient_id))
    for role in ("SYSTEM", "CLINIC_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, (a.clinic_id, patient_id))
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(insert, (a.clinic_id, patient_id))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, b.patient_id))


def test_only_the_last_message_time_of_a_thread_changes(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    touch = "UPDATE messaging.threads SET last_message_at = now() WHERE id = %s"
    assert db.execute(touch, (rows.thread_id,)).rowcount == 1
    assert db.execute(touch, (other_rows.thread_id,)).rowcount == 0
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "UPDATE messaging.threads SET patient_id = patient_id WHERE id = %s",
            (rows.thread_id,),
        )


def test_read_marks_belong_to_their_user(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "messaging.thread_reads") == 1
    assert (
        db.execute("UPDATE messaging.thread_reads SET last_read_at = now()").rowcount
        == 1
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert count(db, "messaging.thread_reads") == 0
    db.execute(
        "INSERT INTO messaging.thread_reads (thread_id, user_id, clinic_id,"
        " last_read_at) VALUES (%s, %s, %s, now())",
        (rows.thread_id, a.doctor_id, a.clinic_id),
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO messaging.thread_reads (thread_id, user_id, clinic_id,"
            " last_read_at) VALUES (%s, %s, %s, now())",
            (rows.thread_id, a.patient_user_id, a.clinic_id),
        )
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO messaging.thread_reads (thread_id, user_id, clinic_id,"
            " last_read_at) VALUES (%s, %s, %s, now())",
            (rows.thread_id, a.doctor_id, a.clinic_id),
        )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    assert count(db, "messaging.thread_reads") == 0
