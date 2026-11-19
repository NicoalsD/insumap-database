"""Create the SQL roles of Insumap.

Roles (Modelo de datos, section 5):

- insumap_migrator: owns every object and runs the migrations. Not superuser,
  no BYPASSRLS.
- insumap_app: the API and the scheduler. DML only, no DDL, subject to RLS.
- insumap_readonly: diagnostics and reports. SELECT on non-secret columns,
  subject to RLS.
- insumap_definer: NOLOGIN owner of the SECURITY DEFINER functions.

Roles are cluster-wide, so they are created only when missing. Login roles are
created WITHOUT a password: passwords are generated once outside the repository
and set by an operator (ALTER ROLE ... PASSWORD), never in a migration.

This revision must run with the bootstrap login (local superuser or the Neon
owner), because it needs CREATEROLE. It grants insumap_migrator to that login
and switches the session to insumap_migrator, so the rest of the chain creates
objects owned by insumap_migrator. migrations/env.py makes the same switch on
later runs.

Revision ID: d7a3a446c395
Revises:
Create Date: 2026-11-18 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7a3a446c395"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the roles, their session limits and the migrator privileges."""
    op.execute(
        """
        DO $roles$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insumap_migrator')
            THEN
                CREATE ROLE insumap_migrator LOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOBYPASSRLS NOREPLICATION;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insumap_app')
            THEN
                CREATE ROLE insumap_app LOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOBYPASSRLS NOREPLICATION;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insumap_readonly')
            THEN
                CREATE ROLE insumap_readonly LOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOBYPASSRLS NOREPLICATION;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insumap_definer')
            THEN
                CREATE ROLE insumap_definer NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOBYPASSRLS NOREPLICATION;
            END IF;
        END
        $roles$;
        """
    )

    # Session limits per role (Modelo de datos, section 5; proposal to be
    # validated in phase 12). They apply on login, not on SET ROLE.
    op.execute(
        """
        ALTER ROLE insumap_app SET statement_timeout = '10s';
        ALTER ROLE insumap_app SET lock_timeout = '3s';
        ALTER ROLE insumap_app SET idle_in_transaction_session_timeout = '15s';
        ALTER ROLE insumap_readonly SET statement_timeout = '30s';
        ALTER ROLE insumap_readonly SET lock_timeout = '3s';
        ALTER ROLE insumap_readonly SET idle_in_transaction_session_timeout = '15s';
        """
    )

    # The migrator creates the schemas, may act as insumap_definer to hand it
    # the SECURITY DEFINER functions, and updates the Alembic version table.
    # The bootstrap login may switch to the migrator with SET ROLE.
    op.execute(
        """
        DO $grants$
        BEGIN
            EXECUTE format(
                'GRANT CREATE ON DATABASE %I TO insumap_migrator',
                current_database()
            );
            IF current_user <> 'insumap_migrator' THEN
                EXECUTE format(
                    'GRANT insumap_migrator TO %I WITH INHERIT FALSE, SET TRUE',
                    current_user
                );
            END IF;
        END
        $grants$;
        GRANT insumap_definer TO insumap_migrator WITH INHERIT TRUE, SET TRUE;
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.alembic_version
            TO insumap_migrator;
        """
    )

    # The remaining revisions of this run create objects owned by the migrator.
    op.execute("SET ROLE insumap_migrator")


def downgrade() -> None:
    """Drop the roles. Only for local databases: roles are cluster-wide."""
    op.execute("RESET ROLE")
    op.execute(
        """
        DO $roles$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'insumap_app', 'insumap_readonly', 'insumap_definer',
                'insumap_migrator'
            ]
            LOOP
                CONTINUE WHEN role_name = current_user
                    OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name);
                EXECUTE format('DROP OWNED BY %I', role_name);
                EXECUTE format('DROP ROLE %I', role_name);
            END LOOP;
        END
        $roles$;
        """
    )
