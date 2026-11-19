"""Create clinic.privacy_notice_for_login(): the notice a patient reads to activate.

GET /public/privacy-notices/{privacy_notice_version_id} (API.md 8.13) is public:
the activation flow shows the notice before there is a session, so there is no
clinic context and row level security hides clinic.privacy_notice_versions.
This SECURITY DEFINER function returns one version by id with the public text
only: no publisher, clinic or other column. Notices are public documents by law
(Cumplimiento-y-privacidad.md); the id is a random UUID v7 handed out by
POST /auth/activation/verify.

Revision ID: d2b6e9f41c03
Revises: 8a3f6c1d2e57
Create Date: 2026-11-19 19:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2b6e9f41c03"
down_revision: str | None = "8a3f6c1d2e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the function, hand it to insumap_definer and grant it to the app."""
    op.execute(
        """
        CREATE FUNCTION clinic.privacy_notice_for_login(p_notice_id uuid)
            RETURNS TABLE (
                id uuid,
                version varchar,
                effective_from timestamptz,
                content text
            )
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
                SELECT n.id, n.version, n.effective_from, n.content
                  FROM clinic.privacy_notice_versions AS n
                 WHERE n.id = p_notice_id
            $$;
        COMMENT ON FUNCTION clinic.privacy_notice_for_login(uuid) IS
            'Public text of one privacy notice version, for the activation flow';

        GRANT SELECT (id, version, effective_from, content)
            ON clinic.privacy_notice_versions TO insumap_definer;
        CREATE POLICY privacy_notice_versions_definer_select
            ON clinic.privacy_notice_versions
            FOR SELECT TO insumap_definer USING (true);

        GRANT CREATE ON SCHEMA clinic TO insumap_definer;
        ALTER FUNCTION clinic.privacy_notice_for_login(uuid) OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA clinic FROM insumap_definer;
        REVOKE ALL ON FUNCTION clinic.privacy_notice_for_login(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION clinic.privacy_notice_for_login(uuid)
            TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the function, its policy and its grant."""
    op.execute(
        """
        DROP FUNCTION clinic.privacy_notice_for_login(uuid);
        DROP POLICY privacy_notice_versions_definer_select
            ON clinic.privacy_notice_versions;
        REVOKE SELECT (id, version, effective_from, content)
            ON clinic.privacy_notice_versions FROM insumap_definer;
        """
    )
