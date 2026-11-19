"""Create ai.dropout_risk_list() and ai.platform_ai_evaluations().

Modelo de datos section 6 (both proposed) and API.md 8.10.

ai.dropout_risk_list() serves GET /dropout-risks to the CLINIC_ADMIN, who has
no row level access to ai.risk_assessments (V8: no clinical data). It returns,
for each patient of the session clinic with a DROPOUT assessment, the latest one
that is not in shadow mode, with the administrative data the API shows: the
patient name, the account status, the days without any record and the tier, the
explanation and the fallback marks of the assessment. Never a glucose, a dose or
any other clinical value. days_without_record counts from the last glucose
reading, insulin dose, meal or physical activity (NULL when the patient never
recorded anything).

ai.platform_ai_evaluations() serves GET /ai-evaluations to the PLATFORM_ADMIN,
who has no RLS branch on ai (MD-15). It returns the evaluations of every clinic
and the global ones: aggregated metrics of simulated cohorts, no personal data.

Both are SECURITY DEFINER, owned by insumap_definer, with search_path fixed to
pg_catalog, and check the role of the session. insumap_definer gets the narrowest
SELECT it needs (by column on the record tables) and a permissive policy TO
insumap_definer on each table it reads; only these functions can use them.

Revision ID: 42a3814bfeaa
Revises: 55d4bea1ee3f
Create Date: 2026-11-19 19:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "42a3814bfeaa"
down_revision: str | None = "55d4bea1ee3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table, timestamp column of its last record
RECORD_COLUMNS = (
    ("glucose_readings", "measured_at"),
    ("insulin_doses", "administered_at"),
    ("meals", "eaten_at"),
    ("physical_activities", "started_at"),
)
LAST_RECORD = "\n".join(
    f"""                          SELECT max(r.{column}) AS at
                            FROM monitoring.{table} AS r
                           WHERE r.patient_id = a.patient_id
                             AND r.clinic_id = v_clinic
                          UNION ALL"""
    for table, column in RECORD_COLUMNS
).removesuffix("UNION ALL")


def upgrade() -> None:
    """Create the functions, the definer access and the grants."""
    grants = "\n".join(
        f"""        GRANT SELECT (patient_id, clinic_id, {column})
            ON monitoring.{table} TO insumap_definer;
        CREATE POLICY {table}_definer_dropout ON monitoring.{table}
            FOR SELECT TO insumap_definer USING (true);"""
        for table, column in RECORD_COLUMNS
    )
    op.execute(
        f"""
        CREATE FUNCTION ai.dropout_risk_list()
            RETURNS TABLE (
                patient_id uuid,
                patient_name varchar,
                account_status varchar,
                days_without_record integer,
                risk_tier varchar,
                explanation jsonb,
                is_fallback boolean,
                fallback_reason varchar,
                generated_at timestamptz
            )
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_clinic uuid := iam.app_clinic_id();
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'CLINIC_ADMIN' OR v_clinic IS NULL
                THEN
                    RAISE EXCEPTION 'only CLINIC_ADMIN reads the dropout risk list';
                END IF;
                RETURN QUERY
                    SELECT a.patient_id,
                           p.full_name,
                           u.status,
                           CASE WHEN last_record.at IS NULL THEN NULL
                                ELSE floor(
                                    extract(epoch FROM now() - last_record.at)
                                    / 86400
                                )::integer
                           END,
                           a.risk_tier,
                           a.explanation,
                           a.is_fallback,
                           a.fallback_reason,
                           a.generated_at
                    FROM (
                        SELECT DISTINCT ON (ra.patient_id)
                               ra.patient_id, ra.risk_tier, ra.explanation,
                               ra.is_fallback, ra.fallback_reason, ra.generated_at
                          FROM ai.risk_assessments AS ra
                         WHERE ra.clinic_id = v_clinic
                           AND ra.assessment_type = 'DROPOUT'
                           AND NOT ra.is_shadow
                         ORDER BY ra.patient_id, ra.generated_at DESC, ra.id DESC
                    ) AS a
                    JOIN clinic.patients AS p
                      ON p.id = a.patient_id AND p.clinic_id = v_clinic
                    JOIN iam.users AS u ON u.id = p.user_id
                    CROSS JOIN LATERAL (
                        SELECT max(x.at) AS at
                          FROM (
{LAST_RECORD}
                          ) AS x
                    ) AS last_record
                    ORDER BY a.patient_id DESC;
            END
            $$;
        COMMENT ON FUNCTION ai.dropout_risk_list() IS
            'Latest dropout assessment per patient of the session clinic for '
            'CLINIC_ADMIN; administrative data, no clinical values';

        CREATE FUNCTION ai.platform_ai_evaluations()
            RETURNS TABLE (
                id uuid,
                clinic_id uuid,
                use_case varchar,
                model_name varchar,
                model_version varchar,
                baseline_name varchar,
                cohort_ref varchar,
                sample_size integer,
                model_metrics jsonb,
                baseline_metrics jsonb,
                fallback_usage_pct numeric,
                evaluated_at timestamptz
            )
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'PLATFORM_ADMIN' THEN
                    RAISE EXCEPTION 'only PLATFORM_ADMIN reads the AI evaluations';
                END IF;
                RETURN QUERY
                    SELECT e.id, e.clinic_id, e.use_case, e.model_name,
                           e.model_version, e.baseline_name, e.cohort_ref,
                           e.sample_size, e.model_metrics, e.baseline_metrics,
                           e.fallback_usage_pct, e.evaluated_at
                    FROM ai.ai_evaluations AS e
                    ORDER BY e.id DESC;
            END
            $$;
        COMMENT ON FUNCTION ai.platform_ai_evaluations() IS
            'AI evaluations of every clinic for PLATFORM_ADMIN; aggregated metrics '
            'only, no personal data';

        GRANT SELECT ON ai.risk_assessments, ai.ai_evaluations
            TO insumap_definer;
        CREATE POLICY risk_assessments_definer_dropout ON ai.risk_assessments
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY ai_evaluations_definer_platform ON ai.ai_evaluations
            FOR SELECT TO insumap_definer USING (true);
{grants}

        GRANT CREATE ON SCHEMA ai TO insumap_definer;
        ALTER FUNCTION ai.dropout_risk_list() OWNER TO insumap_definer;
        ALTER FUNCTION ai.platform_ai_evaluations() OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA ai FROM insumap_definer;
        REVOKE ALL ON FUNCTION ai.dropout_risk_list() FROM PUBLIC;
        REVOKE ALL ON FUNCTION ai.platform_ai_evaluations() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ai.dropout_risk_list() TO insumap_app;
        GRANT EXECUTE ON FUNCTION ai.platform_ai_evaluations() TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the functions, the definer policies and the definer grants."""
    drops = "\n".join(
        f"""        DROP POLICY {table}_definer_dropout ON monitoring.{table};
        REVOKE SELECT (patient_id, clinic_id, {column})
            ON monitoring.{table} FROM insumap_definer;"""
        for table, column in RECORD_COLUMNS
    )
    op.execute(
        f"""
        DROP FUNCTION ai.platform_ai_evaluations();
        DROP FUNCTION ai.dropout_risk_list();
{drops}
        DROP POLICY ai_evaluations_definer_platform ON ai.ai_evaluations;
        DROP POLICY risk_assessments_definer_dropout ON ai.risk_assessments;
        REVOKE SELECT ON ai.risk_assessments, ai.ai_evaluations
            FROM insumap_definer;
        """
    )
