"""ai.dropout_risk_list() and ai.platform_ai_evaluations(): administrative reads."""

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    READONLY_ROLE,
    Tenant,
    act_as,
    add_membership,
    digest,
    make_tenant,
    make_user,
    populate_care,
    populate_monitoring,
    scalar,
)

DROPOUT = (
    "SELECT patient_id, patient_name, account_status, days_without_record,"
    " risk_tier, explanation, is_fallback, fallback_reason, generated_at"
    " FROM ai.dropout_risk_list()"
)
EVALUATIONS = (
    "SELECT id, clinic_id, use_case, sample_size FROM ai.platform_ai_evaluations()"
)
ASSESSMENT = (
    "INSERT INTO ai.risk_assessments (clinic_id, patient_id, assessment_type,"
    " risk_tier, window_days, target_profile_id, model_name, model_version,"
    " input_hash, explanation, is_fallback, fallback_reason, is_shadow,"
    " generated_at) VALUES (%s, %s, 'DROPOUT', %s, 14, %s, 'modelo', '1.0',"
    " %s, %s, %s, %s, %s, %s)"
)


def assess(  # noqa: PLR0913
    db: psycopg.Connection,
    tenant: Tenant,
    profile_id: UUID,
    tier: str,
    generated_at: datetime,
    *,
    fallback: bool = False,
    shadow: bool = False,
) -> None:
    db.execute(
        ASSESSMENT,
        (
            tenant.clinic_id,
            tenant.patient_id,
            tier,
            profile_id,
            digest(f"{tier}{generated_at}{shadow}"),
            '{"factors": ["days_without_record"]}',
            fallback,
            "AI_TIMEOUT" if fallback else None,
            shadow,
            generated_at,
        ),
    )


@pytest.fixture
def world(db: psycopg.Connection) -> tuple[Tenant, Tenant, UUID, UUID]:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    return a, b, populate_care(db, a).profile_id, populate_care(db, b).profile_id


def as_admin(db: psycopg.Connection, tenant: Tenant) -> None:
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.admin_id, role="CLINIC_ADMIN")


def now(db: psycopg.Connection) -> datetime:
    value = scalar(db, "SELECT now()")
    assert isinstance(value, datetime)
    return value


def test_admin_gets_the_latest_dropout_assessment_of_each_patient(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, profile_a, _ = world
    moment = now(db)
    assess(db, a, profile_a, "LOW", moment - timedelta(days=3))
    assess(db, a, profile_a, "HIGH", moment - timedelta(days=1), fallback=True)
    as_admin(db, a)

    rows = db.execute(DROPOUT).fetchall()

    assert len(rows) == 1
    patient_id, name, status, _, tier, explanation, fallback, reason, _ = rows[0]
    assert patient_id == a.patient_id
    assert name == "Paciente MRN-a"
    assert status == "ACTIVE"
    assert tier == "HIGH"
    assert explanation == {"factors": ["days_without_record"]}
    assert fallback is True
    assert reason == "AI_TIMEOUT"


def test_shadow_assessments_and_deterioration_are_not_listed(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, profile_a, _ = world
    db.execute(
        "INSERT INTO ai.risk_assessments (clinic_id, patient_id, assessment_type,"
        " risk_tier, window_days, target_profile_id, model_name, model_version,"
        " input_hash, explanation, generated_at) VALUES (%s, %s, 'DETERIORATION',"
        " 'CRITICAL', 7, %s, 'm', '1', %s, '{}', now())",
        (a.clinic_id, a.patient_id, profile_a, digest("deterioration")),
    )
    assess(db, a, profile_a, "CRITICAL", now(db), shadow=True)
    as_admin(db, a)

    assert db.execute(DROPOUT).fetchall() == []


def test_days_without_record_counts_from_the_last_record(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, profile_a, _ = world
    assess(db, a, profile_a, "MEDIUM", now(db))
    as_admin(db, a)
    assert db.execute(DROPOUT).fetchone()[3] is None  # type: ignore[index]

    db.execute("RESET ROLE")
    populate_monitoring(db, a)
    as_admin(db, a)
    assert db.execute(DROPOUT).fetchone()[3] == 0  # type: ignore[index]


def test_days_without_record_uses_the_newest_of_the_record_tables(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, profile_a, _ = world
    assess(db, a, profile_a, "MEDIUM", now(db))
    db.execute(
        "INSERT INTO monitoring.meals (clinic_id, patient_id, recorded_by,"
        " client_record_id, eaten_at, meal_type, carbs_g)"
        " VALUES (%s, %s, %s, uuidv7(), now() - interval '5 days 2 hours',"
        " 'LUNCH', 40)",
        (a.clinic_id, a.patient_id, a.patient_user_id),
    )
    db.execute(
        "INSERT INTO monitoring.glucose_readings (clinic_id, patient_id,"
        " recorded_by, client_reading_id, measured_at, value_mg_dl, source)"
        " VALUES (%s, %s, %s, uuidv7(), now() - interval '9 days', 100, 'MANUAL')",
        (a.clinic_id, a.patient_id, a.patient_user_id),
    )
    as_admin(db, a)

    assert db.execute(DROPOUT).fetchone()[3] == 5  # type: ignore[index]  # noqa: PLR2004


def test_the_list_is_isolated_by_clinic(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, b, profile_a, profile_b = world
    assess(db, a, profile_a, "LOW", now(db))
    assess(db, b, profile_b, "HIGH", now(db))
    as_admin(db, b)

    rows = db.execute(DROPOUT).fetchall()

    assert [(row[0], row[4]) for row in rows] == [(b.patient_id, "HIGH")]


def test_the_list_carries_no_clinical_column(db: psycopg.Connection) -> None:
    columns = {
        name
        for (name,) in db.execute(
            "SELECT unnest(proargnames) FROM pg_proc"
            " WHERE oid = 'ai.dropout_risk_list()'::regprocedure"
        ).fetchall()
    }
    assert columns == {
        "patient_id",
        "patient_name",
        "account_status",
        "days_without_record",
        "risk_tier",
        "explanation",
        "is_fallback",
        "fallback_reason",
        "generated_at",
    }


@pytest.mark.parametrize("role", ["DOCTOR", "PATIENT", "SYSTEM", "PLATFORM_ADMIN"])
def test_other_roles_are_refused_the_dropout_list(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID], role: str
) -> None:
    a, _, _, _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
    with pytest.raises(errors.RaiseException):
        db.execute(DROPOUT)


def test_the_dropout_list_needs_a_clinic(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, _, _ = world
    act_as(db, user_id=a.admin_id, role="CLINIC_ADMIN")
    with pytest.raises(errors.RaiseException):
        db.execute(DROPOUT)


def make_evaluation(db: psycopg.Connection, clinic_id: UUID | None, size: int) -> None:
    db.execute(
        "INSERT INTO ai.ai_evaluations (clinic_id, use_case, model_name,"
        " model_version, baseline_name, cohort_ref, sample_size, model_metrics,"
        " baseline_metrics) VALUES (%s, 'IA1', 'modelo', '1.0', 'base', 'cohorte',"
        " %s, '{}', '{}')",
        (clinic_id, size),
    )


def test_platform_admin_reads_the_evaluations_of_every_clinic(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, b, _, _ = world
    platform_admin = make_user(db, "platform-ai@example.test")
    add_membership(db, platform_admin, None, "PLATFORM_ADMIN")
    for clinic_id, size in ((a.clinic_id, 10), (b.clinic_id, 20), (None, 30)):
        make_evaluation(db, clinic_id, size)
    act_as(db, user_id=platform_admin, role="PLATFORM_ADMIN")

    rows = db.execute(EVALUATIONS).fetchall()

    assert sorted(row[3] for row in rows) == [10, 20, 30]
    assert {row[1] for row in rows} == {a.clinic_id, b.clinic_id, None}


def test_platform_evaluations_carry_only_aggregated_columns(
    db: psycopg.Connection,
) -> None:
    columns = [
        name
        for (name,) in db.execute(
            "SELECT unnest(proargnames) FROM pg_proc"
            " WHERE oid = 'ai.platform_ai_evaluations()'::regprocedure"
        ).fetchall()
    ]
    assert columns == [
        "id",
        "clinic_id",
        "use_case",
        "model_name",
        "model_version",
        "baseline_name",
        "cohort_ref",
        "sample_size",
        "model_metrics",
        "baseline_metrics",
        "fallback_usage_pct",
        "evaluated_at",
    ]


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "DOCTOR", "PATIENT", "SYSTEM"])
def test_other_roles_are_refused_the_platform_evaluations(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID], role: str
) -> None:
    a, _, _, _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
    with pytest.raises(errors.RaiseException):
        db.execute(EVALUATIONS)


def test_no_context_is_refused_both_functions(db: psycopg.Connection) -> None:
    act_as(db)
    for call in (DROPOUT, EVALUATIONS):
        with pytest.raises(errors.RaiseException), db.transaction():
            db.execute(call)


@pytest.mark.parametrize(
    "signature", ["ai.dropout_risk_list()", "ai.platform_ai_evaluations()"]
)
def test_functions_are_definer_with_a_fixed_search_path(
    db: psycopg.Connection, signature: str
) -> None:
    row = db.execute(
        "SELECT prosecdef, proconfig, pg_get_userbyid(proowner)"
        " FROM pg_proc WHERE oid = %s::regprocedure",
        (signature,),
    ).fetchone()
    assert row == (True, ["search_path=pg_catalog"], "insumap_definer")


@pytest.mark.parametrize(
    "signature", ["ai.dropout_risk_list()", "ai.platform_ai_evaluations()"]
)
def test_only_the_app_executes_the_functions(
    db: psycopg.Connection, signature: str
) -> None:
    def can_execute(role: str) -> object:
        return scalar(
            db,
            "SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')",
            (role, signature),
        )

    assert can_execute(APP_ROLE) is True
    assert can_execute(READONLY_ROLE) is False
    assert can_execute("public") is False


def test_the_app_cannot_read_the_tables_through_the_definer_policies(
    db: psycopg.Connection, world: tuple[Tenant, Tenant, UUID, UUID]
) -> None:
    a, _, profile_a, _ = world
    assess(db, a, profile_a, "LOW", now(db))
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert scalar(db, "SELECT count(*) FROM ai.risk_assessments") == 0
