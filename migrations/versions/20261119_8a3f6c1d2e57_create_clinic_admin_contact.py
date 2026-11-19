"""Create iam.clinic_admin_contact(): where to notify a clinic administrator.

POST /clinics/{clinic_id}/admins/{user_id}/reset-access (API.md 8.3) emails the
holder after iam.reset_clinic_admin_access() runs. The platform administrator
has no clinic context, so row level security hides the user's email; this
SECURITY DEFINER function returns it only for an active CLINIC_ADMIN member of
that clinic and only to a PLATFORM_ADMIN session.

Revision ID: 8a3f6c1d2e57
Revises: 5c1d7e2a4b90
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "8a3f6c1d2e57"
down_revision: str | None = "5c1d7e2a4b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the function, hand it to insumap_definer and grant it to the app."""
    op.execute(
        """
        CREATE FUNCTION iam.clinic_admin_contact(p_clinic_id uuid, p_user_id uuid)
            RETURNS TABLE (email varchar, full_name varchar)
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'PLATFORM_ADMIN' THEN
                    RAISE EXCEPTION 'only PLATFORM_ADMIN reads an admin contact';
                END IF;
                RETURN QUERY
                    SELECT u.email, s.full_name
                      FROM iam.users AS u
                      JOIN iam.memberships AS m ON m.user_id = u.id
                      JOIN iam.roles AS r ON r.id = m.role_id
                      JOIN clinic.staff_profiles AS s
                        ON s.user_id = u.id AND s.clinic_id = m.clinic_id
                     WHERE u.id = p_user_id
                       AND m.clinic_id = p_clinic_id
                       AND m.is_active
                       AND r.code = 'CLINIC_ADMIN';
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'not found';
                END IF;
            END
            $$;
        COMMENT ON FUNCTION iam.clinic_admin_contact(uuid, uuid) IS
            'Email and name of a CLINIC_ADMIN of a clinic, for PLATFORM_ADMIN only';

        GRANT SELECT (user_id, clinic_id, full_name) ON clinic.staff_profiles
            TO insumap_definer;
        CREATE POLICY staff_profiles_definer_select ON clinic.staff_profiles
            FOR SELECT TO insumap_definer USING (true);

        GRANT CREATE ON SCHEMA iam TO insumap_definer;
        ALTER FUNCTION iam.clinic_admin_contact(uuid, uuid) OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA iam FROM insumap_definer;
        REVOKE ALL ON FUNCTION iam.clinic_admin_contact(uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION iam.clinic_admin_contact(uuid, uuid)
            TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the function, its policy and its grant."""
    op.execute(
        """
        DROP FUNCTION iam.clinic_admin_contact(uuid, uuid);
        DROP POLICY staff_profiles_definer_select ON clinic.staff_profiles;
        REVOKE SELECT (user_id, clinic_id, full_name) ON clinic.staff_profiles
            FROM insumap_definer;
        """
    )
