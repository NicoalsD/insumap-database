"""Create the iam, clinic and audit schemas and the RLS helper functions.

Session variables (Modelo de datos, section 4.1), set by the unit of work with
set_config(..., true) at the start of every transaction:

- app.clinic_id: clinic of the session (empty for PLATFORM_ADMIN).
- app.user_id: authenticated user.
- app.role: PLATFORM_ADMIN, CLINIC_ADMIN, DOCTOR, PATIENT or SYSTEM.

The helpers read them with current_setting(..., true): a missing or empty value
yields NULL, so every policy that compares against it returns no rows (fail
closed).

The audit schema only receives audit.reject_mutation() here, because the
append-only tables of iam and clinic need it now; the audit tables come later.

Revision ID: 9bd786a0725c
Revises: d7a3a446c395
Create Date: 2026-11-18 10:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9bd786a0725c"
down_revision: str | None = "d7a3a446c395"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the schemas, the session helpers and the append-only trigger."""
    op.execute(
        """
        CREATE SCHEMA iam;
        CREATE SCHEMA clinic;
        CREATE SCHEMA audit;

        COMMENT ON SCHEMA iam IS 'Identity and access';
        COMMENT ON SCHEMA clinic IS 'Clinics, people and onboarding';
        COMMENT ON SCHEMA audit IS 'Audit and retention';

        GRANT USAGE ON SCHEMA iam, clinic
            TO insumap_app, insumap_readonly, insumap_definer;
        """
    )

    op.execute(
        """
        CREATE FUNCTION iam.app_clinic_id() RETURNS uuid
            LANGUAGE sql STABLE SECURITY INVOKER
            AS $$
                SELECT NULLIF(pg_catalog.current_setting('app.clinic_id', true), '')
                    ::uuid
            $$;
        COMMENT ON FUNCTION iam.app_clinic_id() IS
            'Clinic of the session (app.clinic_id) or NULL when not set';

        CREATE FUNCTION iam.app_user_id() RETURNS uuid
            LANGUAGE sql STABLE SECURITY INVOKER
            AS $$
                SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')
                    ::uuid
            $$;
        COMMENT ON FUNCTION iam.app_user_id() IS
            'Authenticated user of the session (app.user_id) or NULL when not set';

        CREATE FUNCTION iam.app_role() RETURNS varchar
            LANGUAGE sql STABLE SECURITY INVOKER
            AS $$
                SELECT NULLIF(pg_catalog.current_setting('app.role', true), '')
                    ::varchar
            $$;
        COMMENT ON FUNCTION iam.app_role() IS
            'Role of the session (app.role) or NULL when not set';
        """
    )

    op.execute(
        """
        CREATE FUNCTION audit.reject_mutation() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $$
            BEGIN
                -- Only the retention purge may delete, and only from the tables
                -- that have a retention period, with audit.purging set locally.
                IF TG_OP = 'DELETE'
                   AND TG_TABLE_NAME IN ('data_access_log', 'security_events')
                   AND current_setting('audit.purging', true) = 'on'
                THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION '%.% is append-only: % is not allowed',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
                    USING HINT = 'Insert a new row that references the original.';
            END
            $$;
        COMMENT ON FUNCTION audit.reject_mutation() IS
            'BEFORE UPDATE OR DELETE trigger of the append-only tables';
        """
    )


def downgrade() -> None:
    """Drop the helpers and the schemas."""
    op.execute(
        """
        DROP FUNCTION audit.reject_mutation();
        DROP FUNCTION iam.app_role();
        DROP FUNCTION iam.app_user_id();
        DROP FUNCTION iam.app_clinic_id();
        DROP SCHEMA audit;
        DROP SCHEMA clinic;
        DROP SCHEMA iam;
        """
    )
