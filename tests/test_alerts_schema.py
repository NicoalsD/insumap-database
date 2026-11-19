"""Alerts schema: rules, alert lifecycle, notifications and their RLS."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    READONLY_ROLE,
    AlertRows,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    digest,
    make_tenant,
    make_user,
    populate_alerts,
    scalar,
)

TABLES = (
    "alert_rules",
    "alerts",
    "alert_status_history",
    "notifications",
    "notification_deliveries",
    "push_subscriptions",
    "notification_preferences",
)
World = tuple[tuple[Tenant, AlertRows], tuple[Tenant, AlertRows]]


@pytest.fixture
def world(db: psycopg.Connection) -> World:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    return (a, populate_alerts(db, a)), (b, populate_alerts(db, b))


def test_tables_exist(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'alerts'"
    ).fetchall()
    assert {name for (name,) in rows} == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection, world: World, table: str
) -> None:
    assert count(db, f"alerts.{table}") > 0
    act_as(db)
    assert count(db, f"alerts.{table}") == 0


def test_staff_see_alerts_of_their_clinic_only(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (b, _) = world
    for role in ("DOCTOR", "SYSTEM"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        for table in ("alerts", "alert_status_history"):
            assert count(db, f"alerts.{table}") == 1, (role, table)
        # Deliveries are the system's: the doctor does not see them.
        assert count(db, "alerts.notification_deliveries") == int(role == "SYSTEM")
        assert scalar(db, "SELECT array_agg(id) FROM alerts.alerts") == [rows.alert_id]
        assert count(db, "alerts.alert_rules") == 1
        act_as_superuser(db)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert (
        scalar(
            db,
            "SELECT count(*) FROM alerts.alerts WHERE clinic_id = %s",
            (b.clinic_id,),
        )
        == 0
    )


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "PLATFORM_ADMIN"])
def test_administrators_see_no_alerts(
    db: psycopg.Connection, world: World, role: str
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    for table in ("alerts", "alert_status_history", "notification_deliveries"):
        assert count(db, f"alerts.{table}") == 0
    # The administrator reads (but does not see patients' alerts in) the rules.
    assert count(db, "alerts.alert_rules") == (1 if role == "CLINIC_ADMIN" else 0)


def test_patient_sees_own_alerts_history_and_notifications(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, "SELECT array_agg(id) FROM alerts.alerts") == [rows.alert_id]
    assert count(db, "alerts.alert_status_history") == 1
    assert count(db, "alerts.notifications") == 1
    assert count(db, "alerts.notification_deliveries") == 1
    assert count(db, "alerts.push_subscriptions") == 1
    assert count(db, "alerts.notification_preferences") == 1
    assert count(db, "alerts.alert_rules") == 0


def test_staff_do_not_see_each_others_notifications(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    for table in ("notifications", "push_subscriptions"):
        assert count(db, f"alerts.{table}") == 0


def test_clinic_admin_manages_only_its_own_rules(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), (b, _) = world
    insert = (
        "INSERT INTO alerts.alert_rules (clinic_id, code, name, alert_type,"
        " priority, definition, escalation, version)"
        " VALUES (%s, %s, 'Regla', 'HYPERGLYCEMIA', 'LOW', '{}', '[]', 1)"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    db.execute(insert, (a.clinic_id, "OWN_RULE"))
    for clinic_id in (b.clinic_id, None):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, (clinic_id, "OTHER_RULE"))
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id, "DOCTOR_RULE"))


def test_active_rules_are_immutable_and_global_rules_readable(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    db.execute(
        "INSERT INTO alerts.alert_rules (code, name, alert_type, priority,"
        " definition, escalation, version, status)"
        " VALUES ('GLOBAL_RULE', 'Global', 'ADHERENCE', 'LOW', '{}', '[]', 1,"
        " 'ACTIVE')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    assert count(db, "alerts.alert_rules") == 2  # noqa: PLR2004
    act_as_superuser(db)
    with pytest.raises(errors.RaiseException, match="ACTIVE"), db.transaction():
        db.execute(
            "UPDATE alerts.alert_rules SET priority = 'LOW' WHERE id = %s",
            (rows.rule_id,),
        )
    retired = db.execute(
        "UPDATE alerts.alert_rules SET status = 'RETIRED' WHERE id = %s",
        (rows.rule_id,),
    ).rowcount
    assert retired == 1
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO alerts.alert_rules (code, name, alert_type, priority,"
            " definition, escalation, version, status)"
            " VALUES ('GLOBAL_RULE', 'Global', 'ADHERENCE', 'LOW', '{}', '[]', 2,"
            " 'ACTIVE')"
        )


@pytest.mark.parametrize(
    "change",
    [
        "alert_type = 'OTHER'",
        "level = 4",
        "priority = 'URGENT'",
        "definition = '[]'",
        "escalation = '{}'",
        "code = 'lower'",
    ],
)
def test_alert_rule_checks(db: psycopg.Connection, world: World, change: str) -> None:
    (a, rows), _ = world
    draft = scalar(
        db,
        "INSERT INTO alerts.alert_rules (clinic_id, code, name, alert_type,"
        " priority, definition, escalation, version)"
        " VALUES (%s, 'DRAFT_RULE', 'Borrador', 'HYPOGLYCEMIA', 'LOW', '{}', '[]', 1)"
        " RETURNING id",
        (a.clinic_id,),
    )
    assert draft != rows.rule_id
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(f"UPDATE alerts.alert_rules SET {change} WHERE id = %s", (draft,))


def test_only_the_system_creates_alerts(db: psycopg.Connection, world: World) -> None:
    (a, rows), (b, _) = world
    insert = (
        "INSERT INTO alerts.alerts (clinic_id, patient_id, rule_id, alert_type,"
        " priority, status) VALUES (%s, %s, %s, 'HYPOGLYCEMIA', 'HIGH', 'RESOLVED')"
    )
    params = (a.clinic_id, a.patient_id, rows.rule_id)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    db.execute(insert, params)
    for role in ("DOCTOR", "PATIENT", "CLINIC_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role=role)
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, params)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, b.patient_id, rows.rule_id))


def test_one_open_alert_per_patient_and_rule(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    insert = (
        "INSERT INTO alerts.alerts (clinic_id, patient_id, rule_id, alert_type,"
        " priority, status) VALUES (%s, %s, %s, 'HYPOGLYCEMIA', 'HIGH', %s)"
    )
    for status in ("OPEN", "ESCALATED"):
        with pytest.raises(errors.UniqueViolation), db.transaction():
            db.execute(insert, (a.clinic_id, a.patient_id, rows.rule_id, status))
    db.execute(insert, (a.clinic_id, a.patient_id, rows.rule_id, "RESOLVED"))
    db.execute(insert, (a.clinic_id, a.patient_id, rows.rule_id, "ACKNOWLEDGED"))


def test_patient_acknowledges_its_own_alert_but_cannot_resolve_it(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    acknowledge = (
        "UPDATE alerts.alerts SET status = 'ACKNOWLEDGED', acknowledged_by = %s,"
        " acknowledged_at = now() WHERE id = %s"
    )
    assert (
        db.execute(acknowledge, (a.patient_user_id, other_rows.alert_id)).rowcount == 0
    )
    for status in ("RESOLVED", "OPEN", "ESCALATED"):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(
                "UPDATE alerts.alerts SET status = %s WHERE id = %s",
                (status, rows.alert_id),
            )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(acknowledge, (a.doctor_id, rows.alert_id))
    assert db.execute(acknowledge, (a.patient_user_id, rows.alert_id)).rowcount == 1
    # The history row of the acknowledgement is written by the patient.
    history = (
        "INSERT INTO alerts.alert_status_history (clinic_id, alert_id, from_status,"
        " to_status, actor_user_id, actor_type) VALUES (%s, %s, 'OPEN',"
        " 'ACKNOWLEDGED', %s, %s)"
    )
    db.execute(history, (a.clinic_id, rows.alert_id, a.patient_user_id, "USER"))
    for params in (
        (a.clinic_id, rows.alert_id, a.patient_user_id, "SYSTEM"),
        (a.clinic_id, rows.alert_id, a.doctor_id, "USER"),
        (a.clinic_id, other_rows.alert_id, a.patient_user_id, "USER"),
    ):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(history, params)
    # Once acknowledged it is no longer the patient's to change.
    assert (
        db.execute(
            "UPDATE alerts.alerts SET status = 'ACKNOWLEDGED', acknowledged_by = %s"
            " WHERE id = %s",
            (a.patient_user_id, rows.alert_id),
        ).rowcount
        == 0
    )


def test_doctor_resolves_alerts_of_the_clinic_only(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    resolve = (
        "UPDATE alerts.alerts SET status = 'RESOLVED', resolved_by = %s,"
        " resolved_at = now(), resolution_note = 'Paciente contactado' WHERE id = %s"
    )
    assert db.execute(resolve, (a.doctor_id, other_rows.alert_id)).rowcount == 0
    assert db.execute(resolve, (a.doctor_id, rows.alert_id)).rowcount == 1
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "UPDATE alerts.alerts SET resolution_note = 'corto' WHERE id = %s",
            (rows.alert_id,),
        )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_status_history_is_append_only(
    db: psycopg.Connection, world: World, operation: str
) -> None:
    (_, rows), _ = world
    statement = (
        "UPDATE alerts.alert_status_history SET note = note WHERE id = %s"
        if operation == "UPDATE"
        else "DELETE FROM alerts.alert_status_history WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (rows.history_id,))


def test_recipient_reads_and_marks_its_notification(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (_, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, "SELECT array_agg(id) FROM alerts.notifications") == [
        rows.notification_id
    ]
    mark = "UPDATE alerts.notifications SET read_at = now() WHERE id = %s"
    assert db.execute(mark, (rows.notification_id,)).rowcount == 1
    assert db.execute(mark, (other_rows.notification_id,)).rowcount == 0
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "UPDATE alerts.notifications SET message_key = 'x' WHERE id = %s",
            (rows.notification_id,),
        )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO alerts.notifications (clinic_id, recipient_user_id,"
            " notification_type, message_key) VALUES (%s, %s, 'ALERT', 'x')",
            (a.clinic_id, a.patient_user_id),
        )


def test_system_notifies_inside_its_clinic(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), (b, _) = world
    insert = (
        "INSERT INTO alerts.notifications (clinic_id, recipient_user_id,"
        " notification_type, message_key) VALUES (%s, %s, 'REMINDER', 'reminder.x')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    db.execute(insert, (a.clinic_id, a.patient_user_id))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, b.patient_user_id))
    # The system does not read other people's notifications.
    assert count(db, "alerts.notifications") == 0


def test_platform_admin_reads_only_own_notifications(
    db: psycopg.Connection, world: World
) -> None:
    platform_admin = make_user(db, "platform@example.test")
    db.execute(
        "INSERT INTO alerts.notifications (recipient_user_id, notification_type,"
        " message_key) VALUES (%s, 'CONSENT', 'consent.x')",
        (platform_admin,),
    )
    act_as(db, user_id=platform_admin, role="PLATFORM_ADMIN")
    assert count(db, "alerts.notifications") == 1


def test_deliveries_are_written_by_the_system_and_read_by_the_recipient(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    updated = db.execute(
        "UPDATE alerts.notification_deliveries SET status = 'SENT', attempts = 1,"
        " sent_at = now() WHERE id = %s",
        (rows.delivery_id,),
    ).rowcount
    assert updated == 1
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO alerts.notification_deliveries (clinic_id, notification_id,"
            " channel) VALUES (%s, %s, 'IN_APP')",
            (a.clinic_id, rows.notification_id),
        )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO alerts.notification_deliveries (clinic_id, notification_id,"
            " channel) VALUES (%s, %s, 'EMAIL')",
            (a.clinic_id, rows.notification_id),
        )
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert count(db, "alerts.notification_deliveries") == 1
    assert (
        db.execute(
            "UPDATE alerts.notification_deliveries SET status = 'FAILED'"
        ).rowcount
        == 0
    )


def test_subscriptions_belong_to_their_user(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (b, other_rows) = world
    insert = (
        "INSERT INTO alerts.push_subscriptions (clinic_id, user_id, endpoint,"
        " endpoint_hash, p256dh, auth_ciphertext, key_id)"
        " VALUES (%s, %s, %s, %s, 'key', 'secret', %s)"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(
        insert,
        (
            a.clinic_id,
            a.doctor_id,
            "https://push.example.test/d",
            digest("d"),
            "0" * 16,
        ),
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            insert,
            (
                a.clinic_id,
                a.patient_user_id,
                "https://push.example.test/p",
                digest("p"),
                "0" * 16,
            ),
        )
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            insert,
            (
                a.clinic_id,
                a.doctor_id,
                "https://push.example.test/d",
                digest("d"),
                "0" * 16,
            ),
        )
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            insert,
            (
                a.clinic_id,
                a.doctor_id,
                "http://push.example.test/x",
                digest("x"),
                "0" * 16,
            ),
        )
    delete = "DELETE FROM alerts.push_subscriptions WHERE id = %s"
    assert db.execute(delete, (rows.subscription_id,)).rowcount == 0
    assert db.execute(delete, (other_rows.subscription_id,)).rowcount == 0
    act_as(db, clinic_id=b.clinic_id, user_id=b.patient_user_id, role="PATIENT")
    assert db.execute(delete, (other_rows.subscription_id,)).rowcount == 1


def test_system_reads_active_subscriptions_of_its_clinic(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    assert scalar(db, "SELECT array_agg(id) FROM alerts.push_subscriptions") == [
        rows.subscription_id
    ]
    updated = db.execute(
        "UPDATE alerts.push_subscriptions SET is_active = false WHERE id = %s",
        (rows.subscription_id,),
    ).rowcount
    assert updated == 1
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("UPDATE alerts.push_subscriptions SET endpoint_hash = 'x'")


def test_readonly_has_no_access_to_subscriptions(db: psycopg.Connection) -> None:
    act_as(db, READONLY_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("SELECT 1 FROM alerts.push_subscriptions")
    db.execute("SELECT 1 FROM alerts.notifications")


def test_preferences_are_private_and_consistent(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    insert = (
        "INSERT INTO alerts.notification_preferences (user_id, clinic_id,"
        " quiet_start, quiet_end) VALUES (%s, %s, %s, %s)"
    )
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (a.doctor_id, a.clinic_id, "22:00", None))
    db.execute(insert, (a.doctor_id, a.clinic_id, "22:00", "06:00"))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.patient_user_id, a.clinic_id, None, None))
    # SYSTEM reads the preferences of its clinic to decide the channels.
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    assert count(db, "alerts.notification_preferences") == 2  # noqa: PLR2004


def test_app_deletes_only_push_subscriptions(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.role_table_grants"
        " WHERE grantee = %s AND table_schema = 'alerts'"
        "   AND privilege_type = 'DELETE'",
        (APP_ROLE,),
    ).fetchall()
    assert rows == [("push_subscriptions",)]


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE"])
def test_app_cannot_change_the_history(db: psycopg.Connection, privilege: str) -> None:
    assert (
        scalar(
            db,
            "SELECT has_table_privilege(%s, 'alerts.alert_status_history', %s)",
            (APP_ROLE, privilege),
        )
        is False
    )


def test_ids_are_uuids(db: psycopg.Connection, world: World) -> None:
    (_, rows), _ = world
    assert isinstance(rows.alert_id, UUID)
