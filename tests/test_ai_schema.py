"""AI schema: append-only outputs, recommendations, assistant and RLS."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    APP_ROLE,
    READONLY_ROLE,
    AiRows,
    CareRows,
    Tenant,
    act_as,
    act_as_superuser,
    count,
    digest,
    make_tenant,
    populate_ai,
    populate_alerts,
    populate_care,
    scalar,
)

TABLES = (
    "model_versions",
    "glucose_forecasts",
    "risk_assessments",
    "pattern_findings",
    "recommendations",
    "recommendation_decisions",
    "clinical_summaries",
    "assistant_messages",
    "knowledge_documents",
    "ai_evaluations",
)
RLS_TABLES = tuple(table for table in TABLES if table != "model_versions")
APPEND_ONLY = {
    "glucose_forecasts": "forecast_id",
    "risk_assessments": "risk_id",
    "pattern_findings": "pattern_id",
    "recommendation_decisions": "decision_id",
    "clinical_summaries": "summary_id",
    "assistant_messages": "question_id",
}
# Clinical outputs only the clinical staff reads.
STAFF_ONLY = (
    "risk_assessments",
    "pattern_findings",
    "recommendations",
    "recommendation_decisions",
    "clinical_summaries",
)
World = tuple[tuple[Tenant, CareRows, AiRows], tuple[Tenant, CareRows, AiRows]]
KEY = digest("input")


@pytest.fixture
def world(db: psycopg.Connection) -> World:
    built = []
    for key in ("a", "b"):
        tenant = make_tenant(db, key)
        care = populate_care(db, tenant)
        built.append((tenant, care, populate_ai(db, tenant, care)))
    return built[0], built[1]


def test_tables_exist_and_trigram_search_is_installed(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'ai'"
    ).fetchall()
    assert {name for (name,) in rows} == set(TABLES)
    assert (
        scalar(db, "SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'") == 1
    )
    assert (
        scalar(
            db,
            "SELECT count(*) FROM pg_indexes WHERE schemaname = 'ai'"
            " AND indexname = 'ix_knowledge_documents_trgm'",
        )
        == 1
    )


def test_model_versions_is_the_only_table_without_row_level_security(
    db: psycopg.Connection,
) -> None:
    rows = db.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class AS c"
        " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'ai' AND c.relkind = 'r'"
    ).fetchall()
    for name, enabled, forced in rows:
        assert (enabled, forced) == (
            (False, False) if name == "model_versions" else (True, True)
        ), name


@pytest.mark.parametrize("table", RLS_TABLES)
def test_no_context_sees_no_rows(
    db: psycopg.Connection, world: World, table: str
) -> None:
    assert count(db, f"ai.{table}") > 0
    act_as(db)
    assert count(db, f"ai.{table}") == 0


@pytest.mark.parametrize("table", RLS_TABLES)
def test_staff_see_only_their_clinic(
    db: psycopg.Connection, world: World, table: str
) -> None:
    (a, _, _), _ = world
    visible = {
        "DOCTOR": {
            "glucose_forecasts": 1,
            "risk_assessments": 1,
            "pattern_findings": 1,
            "recommendations": 1,
            "recommendation_decisions": 1,
            "clinical_summaries": 1,
            "assistant_messages": 0,
            "knowledge_documents": 2,
            "ai_evaluations": 0,
        },
        "SYSTEM": {
            "glucose_forecasts": 1,
            "risk_assessments": 1,
            "pattern_findings": 1,
            "recommendations": 1,
            "recommendation_decisions": 1,
            "clinical_summaries": 1,
            "assistant_messages": 2,
            "knowledge_documents": 1,
            "ai_evaluations": 0,
        },
    }
    for role, expected in visible.items():
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        assert count(db, f"ai.{table}") == expected[table], (role, table)
        act_as_superuser(db)


@pytest.mark.parametrize("table", STAFF_ONLY)
@pytest.mark.parametrize("role", ["PATIENT", "CLINIC_ADMIN", "PLATFORM_ADMIN"])
def test_others_do_not_read_clinical_outputs(
    db: psycopg.Connection, world: World, table: str, role: str
) -> None:
    (a, _, _), _ = world
    user = a.patient_user_id if role == "PATIENT" else a.admin_id
    act_as(db, clinic_id=a.clinic_id, user_id=user, role=role)
    assert count(db, f"ai.{table}") == 0


def test_patient_reads_own_forecasts_and_conversation_only(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, rows), _ = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    assert scalar(db, "SELECT array_agg(id) FROM ai.glucose_forecasts") == [
        rows.forecast_id
    ]
    assert count(db, "ai.assistant_messages") == 2  # noqa: PLR2004
    # Only approved material reaches the patient.
    assert scalar(db, "SELECT array_agg(id) FROM ai.knowledge_documents") == [
        rows.document_id
    ]
    assert count(db, "ai.ai_evaluations") == 0


def test_model_versions_are_registered_by_the_app(db: psycopg.Connection) -> None:
    act_as(db, APP_ROLE)
    insert = (
        "INSERT INTO ai.model_versions (use_case, model_name, model_version)"
        " VALUES ('IA1', 'modelo', '1.0.0+abc')"
    )
    db.execute(insert)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert)
    db.execute("UPDATE ai.model_versions SET status = 'ACTIVE', activated_at = now()")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("UPDATE ai.model_versions SET model_name = 'otro'")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("DELETE FROM ai.model_versions")
    act_as(db, READONLY_ROLE)
    assert count(db, "ai.model_versions") == 1


@pytest.mark.parametrize(("table", "row"), sorted(APPEND_ONLY.items()))
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_outputs_are_append_only(
    db: psycopg.Connection, world: World, table: str, row: str, operation: str
) -> None:
    (_, _, rows), _ = world
    statement = (
        f"UPDATE ai.{table} SET clinic_id = clinic_id WHERE id = %s"
        if operation == "UPDATE"
        else f"DELETE FROM ai.{table} WHERE id = %s"
    )
    with pytest.raises(errors.RaiseException, match="append-only"), db.transaction():
        db.execute(statement, (getattr(rows, row),))


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE"])
def test_app_cannot_update_or_delete_outputs(
    db: psycopg.Connection, table: str, privilege: str
) -> None:
    assert (
        scalar(
            db,
            "SELECT has_table_privilege(%s, %s, %s)",
            (APP_ROLE, f"ai.{table}", privilege),
        )
        is False
    )


def test_only_the_system_writes_outputs(db: psycopg.Connection, world: World) -> None:
    (a, care, _), (b, _, _) = world
    insert = (
        "INSERT INTO ai.risk_assessments (clinic_id, patient_id, assessment_type,"
        " window_days, target_profile_id, model_name, model_version, input_hash,"
        " explanation, generated_at)"
        " VALUES (%s, %s, 'DROPOUT', 14, %s, 'm', 'v', %s, '{}', now())"
    )
    params = (a.clinic_id, a.patient_id, care.profile_id, KEY)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    db.execute(insert, params)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, b.patient_id, care.profile_id, KEY))
    for role in ("DOCTOR", "PATIENT", "CLINIC_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(insert, params)


@pytest.mark.parametrize(
    ("is_fallback", "reason", "explanation"),
    [
        (True, None, "{}"),
        (False, "AI_ERROR", "{}"),
        (True, "UNKNOWN", "{}"),
        (False, None, "[]"),
    ],
)
def test_output_fallback_rules(
    db: psycopg.Connection,
    world: World,
    is_fallback: bool,
    reason: str | None,
    explanation: str,
) -> None:
    (a, care, _), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO ai.risk_assessments (clinic_id, patient_id, assessment_type,"
            " window_days, target_profile_id, model_name, model_version, input_hash,"
            " generated_at, is_fallback, fallback_reason, explanation)"
            " VALUES (%s, %s, 'DETERIORATION', 7, %s, 'm', 'v', %s, now(), %s, %s,"
            " %s::jsonb)",
            (
                a.clinic_id,
                a.patient_id,
                care.profile_id,
                KEY,
                is_fallback,
                reason,
                explanation,
            ),
        )


def test_recommendations_always_need_the_doctor(
    db: psycopg.Connection, world: World
) -> None:
    (a, care, _), _ = world
    insert = (
        "INSERT INTO ai.recommendations (clinic_id, patient_id, plan_id, action,"
        " suggestion, target_profile_id, requires_doctor_approval, proposed_at,"
        " expires_at, model_name, model_version, input_hash, explanation,"
        " generated_at) VALUES (%s, %s, %s, 'DECREASE', '{}', %s, %s, now(),"
        " now() + %s * interval '1 day', 'm', 'v', %s, '{}', now())"
    )
    base = (a.clinic_id, a.patient_id, care.plan_id, care.profile_id)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (*base, False, 1, KEY))
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (*base, True, -1, KEY))
    db.execute(insert, (*base, True, 1, KEY))
    # One pending recommendation per plan and action.
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(insert, (*base, True, 1, KEY))


def test_doctor_decides_but_does_not_create_recommendations(
    db: psycopg.Connection, world: World
) -> None:
    (a, care, rows), (_, _, other_rows) = world
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    decide = "UPDATE ai.recommendations SET status = %s WHERE id = %s"
    assert db.execute(decide, ("APPROVED", other_rows.recommendation_id)).rowcount == 0
    assert db.execute(decide, ("APPROVED", rows.recommendation_id)).rowcount == 1
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(
            "INSERT INTO ai.recommendations (clinic_id, patient_id, plan_id,"
            " action, suggestion, target_profile_id, expires_at, model_name,"
            " model_version, input_hash, explanation, generated_at)"
            " VALUES (%s, %s, %s, 'DECREASE', '{}', %s, now() + interval '1 day',"
            " 'm', 'v', %s, '{}', now())",
            (a.clinic_id, a.patient_id, care.plan_id, care.profile_id, KEY),
        )
    for role in ("PATIENT", "CLINIC_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role=role)
        assert db.execute(decide, ("REJECTED", rows.recommendation_id)).rowcount == 0


@pytest.mark.parametrize(
    ("decision", "reason", "modified"),
    [
        ("REJECTED", None, None),
        ("APPROVED_WITH_CHANGES", None, "'{}'"),
        ("APPROVED_WITH_CHANGES", "'Ajuste menor del medico'", None),
        ("APPROVED", None, "'{}'"),
        ("REJECTED", "'corto'", None),
        ("PENDING", None, None),
    ],
)
def test_decision_rules(
    db: psycopg.Connection,
    world: World,
    decision: str,
    reason: str | None,
    modified: str | None,
) -> None:
    (a, _, rows), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO ai.recommendation_decisions (clinic_id, recommendation_id,"
            " decision, decided_by, decided_at, reason, modified_suggestion,"
            f" model_version) VALUES (%s, %s, %s, %s, now(), {reason or 'NULL'},"
            f" {modified or 'NULL'}, 'v')",
            (a.clinic_id, rows.recommendation_id, decision, a.doctor_id),
        )


def test_doctor_records_decisions_in_its_clinic_only(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, rows), (b, _, other_rows) = world
    insert = (
        "INSERT INTO ai.recommendation_decisions (clinic_id, recommendation_id,"
        " decision, decided_by, decided_at, reason, model_version)"
        " VALUES (%s, %s, 'REJECTED', %s, now(), 'No es oportuno ahora', 'v')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    db.execute(insert, (a.clinic_id, rows.recommendation_id, a.doctor_id))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, other_rows.recommendation_id, a.doctor_id))


def test_patient_writes_questions_and_the_system_writes_answers(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, _), (b, _, _) = world
    question = (
        "INSERT INTO ai.assistant_messages (clinic_id, patient_id, conversation_id,"
        " sender, content) VALUES (%s, %s, uuidv7(), %s, 'Hola')"
    )
    answer = (
        "INSERT INTO ai.assistant_messages (clinic_id, patient_id, conversation_id,"
        " sender, content, model_name, model_version, input_hash, explanation,"
        " escalation_level) VALUES (%s, %s, uuidv7(), 'ASSISTANT', 'Hola', 'm',"
        " 'v', %s, '{}', 'NONE')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.patient_user_id, role="PATIENT")
    db.execute(question, (a.clinic_id, a.patient_id, "PATIENT"))
    for statement, params in (
        (question, (a.clinic_id, a.patient_id, "ASSISTANT")),
        (answer, (a.clinic_id, a.patient_id, KEY)),
        (question, (a.clinic_id, b.patient_id, "PATIENT")),
        (question, (b.clinic_id, b.patient_id, "PATIENT")),
    ):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(statement, params)
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    db.execute(answer, (a.clinic_id, a.patient_id, KEY))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(question, (a.clinic_id, a.patient_id, "PATIENT"))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(answer, (b.clinic_id, b.patient_id, KEY))


def test_a_patient_message_carries_no_model_data(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, _), _ = world
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO ai.assistant_messages (clinic_id, patient_id,"
            " conversation_id, sender, content, model_name)"
            " VALUES (%s, %s, uuidv7(), 'PATIENT', 'Hola', 'm')",
            (a.clinic_id, a.patient_id),
        )
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO ai.assistant_messages (clinic_id, patient_id,"
            " conversation_id, sender, content, escalation_level)"
            " VALUES (%s, %s, uuidv7(), 'ASSISTANT', 'Hola', 'PANIC')",
            (a.clinic_id, a.patient_id),
        )


def test_clinic_admin_manages_knowledge_documents(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, _), (b, _, _) = world
    insert = (
        "INSERT INTO ai.knowledge_documents (clinic_id, title, body)"
        " VALUES (%s, 'Nuevo', 'Texto')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    assert count(db, "ai.knowledge_documents") == 2  # noqa: PLR2004
    db.execute(insert, (a.clinic_id,))
    assert (
        db.execute(
            "UPDATE ai.knowledge_documents SET status = 'APPROVED', approved_by = %s,"
            " approved_at = now() WHERE title = 'Nuevo'",
            (a.admin_id,),
        ).rowcount
        == 1
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id,))
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="DOCTOR")
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (a.clinic_id,))
    with pytest.raises(errors.CheckViolation), db.transaction():
        act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
        db.execute("UPDATE ai.knowledge_documents SET status = 'LIVE'")


def test_evaluations_are_written_by_the_system_and_read_by_the_admin(
    db: psycopg.Connection, world: World
) -> None:
    (a, _, _), (b, _, _) = world
    insert = (
        "INSERT INTO ai.ai_evaluations (clinic_id, use_case, model_name,"
        " model_version, baseline_name, cohort_ref, sample_size, model_metrics,"
        " baseline_metrics) VALUES (%s, 'IA2', 'm', 'v', 'base', 'cohorte', %s,"
        " '{}', '{}')"
    )
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role="SYSTEM")
    db.execute(insert, (None, 5))
    db.execute(insert, (a.clinic_id, 5))
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(insert, (b.clinic_id, 5))
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(insert, (a.clinic_id, 0))
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")
    # Global rows plus the clinic's own, never another clinic's.
    assert count(db, "ai.ai_evaluations") == 3  # noqa: PLR2004
    for role in ("DOCTOR", "PATIENT", "PLATFORM_ADMIN"):
        act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role=role)
        assert count(db, "ai.ai_evaluations") == 0


def test_circular_references_are_closed(db: psycopg.Connection, world: World) -> None:
    (a, care, rows), _ = world
    alert_rows = populate_alerts(db, a)
    fake = scalar(db, "SELECT uuidv7()")
    cases = (
        ("care.treatment_plans", "source_recommendation_id", care.draft_plan_id),
        ("care.follow_ups", "risk_assessment_id", care.follow_up_id),
        ("alerts.alerts", "forecast_id", alert_rows.alert_id),
    )
    for table, column, row_id in cases:
        with pytest.raises(errors.ForeignKeyViolation), db.transaction():
            db.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s", (fake, row_id)
            )
    # The triage review is append-only, so only the constraint is checked below.
    # The valid references work.
    db.execute(
        "UPDATE care.treatment_plans SET source_recommendation_id = %s WHERE id = %s",
        (rows.recommendation_id, care.draft_plan_id),
    )
    db.execute(
        "UPDATE alerts.alerts SET forecast_id = %s WHERE id = %s",
        (rows.forecast_id, alert_rows.alert_id),
    )
    constraint = scalar(
        db,
        "SELECT count(*) FROM pg_constraint WHERE conname IN ("
        "'fk_triage_reviews_risk_assessment_id', 'fk_follow_ups_risk_assessment_id',"
        " 'fk_treatment_plans_source_recommendation_id', 'fk_alerts_forecast_id')",
    )
    assert constraint == 4  # noqa: PLR2004
