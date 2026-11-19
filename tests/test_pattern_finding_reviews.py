"""ai.pattern_finding_reviews: dismissing a finding, append-only and RLS."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    READONLY_ROLE,
    AiRows,
    Tenant,
    act_as,
    count,
    make_tenant,
    populate_ai,
    populate_care,
    scalar,
)

INSERT = (
    "INSERT INTO ai.pattern_finding_reviews (clinic_id, patient_id, finding_id,"
    " action, reason, reviewed_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id"
)
World = tuple[tuple[Tenant, AiRows], tuple[Tenant, AiRows]]


@pytest.fixture
def world(db: psycopg.Connection) -> World:
    built = []
    for key in ("a", "b"):
        tenant = make_tenant(db, key)
        built.append((tenant, populate_ai(db, tenant, populate_care(db, tenant))))
    return built[0], built[1]


def dismiss(  # noqa: PLR0913
    db: psycopg.Connection,
    tenant: Tenant,
    finding_id: UUID,
    *,
    reason: str | None = "Falso positivo",
    action: str = "DISMISSED",
    reviewed_by: UUID | None = None,
) -> UUID:
    value = scalar(
        db,
        INSERT,
        (
            tenant.clinic_id,
            tenant.patient_id,
            finding_id,
            action,
            reason,
            reviewed_by or tenant.doctor_id,
        ),
    )
    assert isinstance(value, UUID)
    return value


def as_doctor(db: psycopg.Connection, tenant: Tenant) -> None:
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.doctor_id, role="DOCTOR")


def test_the_table_has_the_contract_columns(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT column_name, data_type, character_maximum_length, is_nullable"
        " FROM information_schema.columns"
        " WHERE table_schema = 'ai' AND table_name = 'pattern_finding_reviews'"
    ).fetchall()
    columns = {name: (kind, size, nullable) for name, kind, size, nullable in rows}
    assert columns == {
        "id": ("uuid", None, "NO"),
        "clinic_id": ("uuid", None, "NO"),
        "patient_id": ("uuid", None, "NO"),
        "finding_id": ("uuid", None, "NO"),
        "action": ("character varying", 9, "NO"),
        "reason": ("character varying", 500, "YES"),
        "reviewed_by": ("uuid", None, "NO"),
        "reviewed_at": ("timestamp with time zone", None, "NO"),
    }


def test_the_doctor_dismisses_a_finding_and_reads_it(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    as_doctor(db, a)
    review_id = dismiss(db, a, rows.pattern_id)
    row = db.execute(
        "SELECT finding_id, action, reason, reviewed_by, reviewed_at IS NOT NULL"
        " FROM ai.pattern_finding_reviews WHERE id = %s",
        (review_id,),
    ).fetchone()
    assert row == (rows.pattern_id, "DISMISSED", "Falso positivo", a.doctor_id, True)


def test_a_dismissal_without_reason_is_accepted(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    as_doctor(db, a)
    dismiss(db, a, rows.pattern_id, reason=None)


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_an_empty_or_too_long_reason_is_rejected(
    db: psycopg.Connection, world: World, reason: str
) -> None:
    (a, rows), _ = world
    with pytest.raises((errors.CheckViolation, errors.StringDataRightTruncation)):
        with db.transaction():
            dismiss(db, a, rows.pattern_id, reason=reason)


@pytest.mark.parametrize("action", ["REVIEWED", "dismissed", ""])
def test_an_action_outside_the_catalog_is_rejected(
    db: psycopg.Connection, world: World, action: str
) -> None:
    (a, rows), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        dismiss(db, a, rows.pattern_id, action=action)


def test_a_finding_is_dismissed_only_once(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    as_doctor(db, a)
    dismiss(db, a, rows.pattern_id)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        dismiss(db, a, rows.pattern_id)


def test_the_review_cannot_name_another_patient_or_clinic_than_its_finding(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), (b, _) = world
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        scalar(
            db,
            INSERT,
            (
                a.clinic_id,
                b.patient_id,
                rows.pattern_id,
                "DISMISSED",
                None,
                a.doctor_id,
            ),
        )
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        scalar(
            db,
            INSERT,
            (
                b.clinic_id,
                a.patient_id,
                rows.pattern_id,
                "DISMISSED",
                None,
                a.doctor_id,
            ),
        )


def test_the_reviewer_must_be_a_user(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        dismiss(
            db,
            a,
            rows.pattern_id,
            reviewed_by=UUID("00000000-0000-7000-8000-000000000000"),
        )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_reviews_are_append_only(
    db: psycopg.Connection, world: World, operation: str
) -> None:
    (a, rows), _ = world
    review_id = dismiss(db, a, rows.pattern_id)
    statement = (
        "UPDATE ai.pattern_finding_reviews SET reason = 'otro' WHERE id = %s"
        if operation == "UPDATE"
        else "DELETE FROM ai.pattern_finding_reviews WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (review_id,))


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE"])
def test_app_cannot_update_or_delete_reviews(
    db: psycopg.Connection, privilege: str
) -> None:
    assert (
        scalar(
            db,
            "SELECT has_table_privilege(%s, 'ai.pattern_finding_reviews', %s)",
            (APP_ROLE, privilege),
        )
        is False
    )


def test_the_trigger_exists(db: psycopg.Connection) -> None:
    assert (
        scalar(
            db,
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal"
            " AND tgname = 'tg_pattern_finding_reviews_append_only'",
        )
        == 1
    )


def test_rls_is_enabled_and_forced(db: psycopg.Connection) -> None:
    row = db.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE oid = 'ai.pattern_finding_reviews'::regclass"
    ).fetchone()
    assert row == (True, True)


def test_no_context_sees_nothing(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    dismiss(db, a, rows.pattern_id)
    assert count(db, "ai.pattern_finding_reviews") == 1
    act_as(db)
    assert count(db, "ai.pattern_finding_reviews") == 0


def test_reads_are_isolated_by_clinic(db: psycopg.Connection, world: World) -> None:
    (a, rows), (b, other_rows) = world
    dismiss(db, a, rows.pattern_id)
    dismiss(db, b, other_rows.pattern_id, reviewed_by=b.doctor_id)
    for role in ("DOCTOR", "SYSTEM"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        assert count(db, "ai.pattern_finding_reviews") == 1, role
        db.execute("RESET ROLE")


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "PATIENT", "PLATFORM_ADMIN"])
def test_other_roles_read_nothing(
    db: psycopg.Connection, world: World, role: str
) -> None:
    (a, rows), _ = world
    dismiss(db, a, rows.pattern_id)
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    assert count(db, "ai.pattern_finding_reviews") == 0


def test_the_doctor_cannot_dismiss_in_another_clinic(
    db: psycopg.Connection, world: World
) -> None:
    (a, _), (b, other_rows) = world
    as_doctor(db, a)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        dismiss(db, b, other_rows.pattern_id, reviewed_by=a.doctor_id)


def test_the_doctor_dismisses_only_as_itself(
    db: psycopg.Connection, world: World
) -> None:
    (a, rows), _ = world
    as_doctor(db, a)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        dismiss(db, a, rows.pattern_id, reviewed_by=a.admin_id)


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "PATIENT", "SYSTEM"])
def test_only_a_doctor_dismisses(
    db: psycopg.Connection, world: World, role: str
) -> None:
    (a, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        dismiss(db, a, rows.pattern_id)


def test_readonly_reads_but_cannot_write(db: psycopg.Connection, world: World) -> None:
    (a, rows), _ = world
    dismiss(db, a, rows.pattern_id)
    act_as(db, READONLY_ROLE, clinic_id=a.clinic_id, user_id=a.admin_id, role="DOCTOR")
    assert count(db, "ai.pattern_finding_reviews") == 1
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        dismiss(db, a, rows.pattern_id)
