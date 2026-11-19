"""Create alerts.clinic_alert_summary(): alert counts for the clinic administrator.

Modelo de datos 9.5 and the function table of section 6. The clinic
administrator has no row level access to alerts.alerts (no clinical data, V8),
so the summary of GET /alerts/summary (alerts.read_summary) comes from this
SECURITY DEFINER function: counts only, never values. One row with a null
doctor_id carries the clinic totals; one row per primary doctor carries the
alerts of his patients that nobody has acknowledged yet.

Revision ID: 5c1d7e2a4b90
Revises: e30f0bdf0247
Create Date: 2026-11-19 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5c1d7e2a4b90"
down_revision: str | None = "e30f0bdf0247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the function, hand it to insumap_definer and grant it to the app."""
    op.execute(
        """
        CREATE FUNCTION alerts.clinic_alert_summary()
            RETURNS TABLE (
                doctor_id uuid,
                open_count bigint,
                escalated_count bigint,
                unattended_count bigint
            )
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_clinic uuid := iam.app_clinic_id();
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'CLINIC_ADMIN' OR v_clinic IS NULL
                THEN
                    RAISE EXCEPTION 'only CLINIC_ADMIN reads the alert summary';
                END IF;
                RETURN QUERY
                    SELECT NULL::uuid,
                           count(*) FILTER (WHERE a.status <> 'RESOLVED'),
                           count(*) FILTER (WHERE a.status = 'ESCALATED'),
                           count(*) FILTER (
                               WHERE a.status IN ('OPEN', 'ESCALATED')
                           )
                    FROM alerts.alerts AS a
                    WHERE a.clinic_id = v_clinic;
                RETURN QUERY
                    SELECT t.doctor_user_id,
                           0::bigint,
                           0::bigint,
                           count(a.id)
                    FROM clinic.care_team_assignments AS t
                    LEFT JOIN alerts.alerts AS a
                        ON a.patient_id = t.patient_id
                       AND a.clinic_id = v_clinic
                       AND a.status IN ('OPEN', 'ESCALATED')
                    WHERE t.clinic_id = v_clinic
                      AND t.is_primary
                      AND t.ended_at IS NULL
                    GROUP BY t.doctor_user_id;
            END
            $$;
        COMMENT ON FUNCTION alerts.clinic_alert_summary() IS
            'Alert counts of the session clinic for CLINIC_ADMIN; counts only';

        GRANT SELECT ON alerts.alerts, clinic.care_team_assignments
            TO insumap_definer;
        CREATE POLICY alerts_definer_summary ON alerts.alerts
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY care_team_assignments_definer_summary
            ON clinic.care_team_assignments
            FOR SELECT TO insumap_definer USING (true);

        GRANT CREATE ON SCHEMA alerts TO insumap_definer;
        ALTER FUNCTION alerts.clinic_alert_summary() OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA alerts FROM insumap_definer;
        REVOKE ALL ON FUNCTION alerts.clinic_alert_summary() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION alerts.clinic_alert_summary() TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the function, its policies and grants."""
    op.execute(
        """
        DROP FUNCTION alerts.clinic_alert_summary();
        DROP POLICY care_team_assignments_definer_summary
            ON clinic.care_team_assignments;
        DROP POLICY alerts_definer_summary ON alerts.alerts;
        REVOKE SELECT ON alerts.alerts, clinic.care_team_assignments
            FROM insumap_definer;
        """
    )
