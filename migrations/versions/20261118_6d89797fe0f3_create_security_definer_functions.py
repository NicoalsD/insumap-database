"""Create the SECURITY DEFINER functions of the authentication slice.

Modelo de datos 4.2, 4.4 and 6; Seguridad 6.2, point 8. Login, activation and
refresh read before the clinic and the user are known, so they go through
functions that return only the minimum columns:

- iam.lookup_login_identity(p_email_normalized)
- iam.lookup_invitation_by_code_hash(p_code_hash)
- iam.lookup_refresh_token(p_token_hash)
- clinic.current_patient_id(), used by the RLS-2 policies.
- clinic.on_clinic_suspended(), trigger that revokes every refresh family of a
  suspended clinic.

Every function belongs to insumap_definer (NOLOGIN, NOBYPASSRLS) and fixes its
search_path. FORCE ROW LEVEL SECURITY also binds the definer, so the tables it
reads get a permissive policy TO insumap_definer; the filter is in the
function. EXECUTE is revoked from PUBLIC and granted to insumap_app only (and
current_patient_id also to insumap_readonly, because policies call it).

Revision ID: 6d89797fe0f3
Revises: 6db90c757a50
Create Date: 2026-11-18 10:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "6d89797fe0f3"
down_revision: str | None = "6db90c757a50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the definer functions, their privileges and policies."""
    # Only the reads and writes each function needs.
    op.execute(
        """
        GRANT SELECT ON iam.users, iam.memberships, iam.roles,
            iam.credential_invitations, iam.refresh_tokens,
            clinic.clinics, clinic.patients
            TO insumap_definer;
        GRANT UPDATE (revoked_at, revoked_reason) ON iam.refresh_tokens
            TO insumap_definer;

        CREATE POLICY users_definer_select ON iam.users
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY memberships_definer_select ON iam.memberships
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY credential_invitations_definer_select
            ON iam.credential_invitations
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY refresh_tokens_definer_select ON iam.refresh_tokens
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY refresh_tokens_definer_update ON iam.refresh_tokens
            FOR UPDATE TO insumap_definer USING (true) WITH CHECK (true);
        CREATE POLICY clinics_definer_select ON clinic.clinics
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY patients_definer_select ON clinic.patients
            FOR SELECT TO insumap_definer USING (true);
        """
    )

    op.execute(
        """
        CREATE FUNCTION iam.lookup_login_identity(p_email_normalized text)
            RETURNS TABLE (
                user_id uuid,
                status varchar,
                password_hash varchar,
                token_version integer,
                role varchar,
                clinic_id uuid,
                clinic_status varchar
            )
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, iam
            AS $$
                -- Role and clinic come from the active membership only; a user
                -- without one gets NULL role and clinic.
                SELECT u.id, u.status, u.password_hash, u.token_version,
                       r.code, m.clinic_id, c.status
                  FROM iam.users AS u
                  LEFT JOIN iam.memberships AS m
                    ON m.user_id = u.id AND m.is_active
                  LEFT JOIN iam.roles AS r ON r.id = m.role_id
                  LEFT JOIN clinic.clinics AS c ON c.id = m.clinic_id
                 WHERE p_email_normalized IS NOT NULL
                   AND u.email_normalized = p_email_normalized
            $$;
        COMMENT ON FUNCTION iam.lookup_login_identity(text) IS
            'Login lookup before the context exists (LoginUseCase)';

        CREATE FUNCTION iam.lookup_invitation_by_code_hash(p_code_hash text)
            RETURNS TABLE (
                invitation_id uuid,
                clinic_id uuid,
                user_id uuid,
                purpose varchar,
                status varchar,
                expires_at timestamptz,
                failed_attempts smallint
            )
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, iam
            AS $$
                SELECT ci.id, ci.clinic_id, ci.user_id, ci.purpose, ci.status,
                       ci.expires_at, ci.failed_attempts
                  FROM iam.credential_invitations AS ci
                 WHERE p_code_hash ~ '^[0-9a-f]{64}$'
                   AND ci.code_hash = p_code_hash::bpchar
            $$;
        COMMENT ON FUNCTION iam.lookup_invitation_by_code_hash(text) IS
            'Activation lookup by code HMAC before the context exists';

        CREATE FUNCTION iam.lookup_refresh_token(p_token_hash text)
            RETURNS TABLE (
                token_id uuid,
                user_id uuid,
                clinic_id uuid,
                role varchar,
                family_id uuid,
                expires_at timestamptz,
                family_expires_at timestamptz,
                revoked_at timestamptz,
                revoked_reason varchar
            )
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, iam
            AS $$
                SELECT t.id, t.user_id, t.clinic_id, r.code, t.family_id,
                       t.expires_at, t.family_expires_at, t.revoked_at,
                       t.revoked_reason
                  FROM iam.refresh_tokens AS t
                  LEFT JOIN iam.memberships AS m
                    ON m.user_id = t.user_id AND m.is_active
                  LEFT JOIN iam.roles AS r ON r.id = m.role_id
                 WHERE p_token_hash ~ '^[0-9a-f]{64}$'
                   AND t.token_hash = p_token_hash::bpchar
            $$;
        COMMENT ON FUNCTION iam.lookup_refresh_token(text) IS
            'Refresh lookup by token SHA-256 before the context exists';

        CREATE FUNCTION clinic.current_patient_id() RETURNS uuid
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, clinic
            AS $$
                SELECT p.id
                  FROM clinic.patients AS p
                 WHERE p.user_id = iam.app_user_id()
                   AND p.clinic_id = iam.app_clinic_id()
            $$;
        COMMENT ON FUNCTION clinic.current_patient_id() IS
            'Patient of the session user in the session clinic (RLS-2 policies)';

        CREATE FUNCTION clinic.on_clinic_suspended() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, clinic
            AS $$
            BEGIN
                UPDATE iam.refresh_tokens
                   SET revoked_at = now(), revoked_reason = 'CLINIC_SUSPENDED'
                 WHERE clinic_id = NEW.id
                   AND revoked_at IS NULL;
                RETURN NULL;
            END
            $$;
        COMMENT ON FUNCTION clinic.on_clinic_suspended() IS
            'Revokes every refresh family of a clinic that becomes SUSPENDED';
        CREATE TRIGGER tg_clinics_on_suspended
            AFTER UPDATE OF status ON clinic.clinics
            FOR EACH ROW
            WHEN (NEW.status = 'SUSPENDED' AND OLD.status <> 'SUSPENDED')
            EXECUTE FUNCTION clinic.on_clinic_suspended();
        """
    )

    # Hand the functions to insumap_definer. The new owner needs CREATE on the
    # schema only while the ownership changes.
    op.execute(
        """
        GRANT CREATE ON SCHEMA iam, clinic TO insumap_definer;
        ALTER FUNCTION iam.lookup_login_identity(text) OWNER TO insumap_definer;
        ALTER FUNCTION iam.lookup_invitation_by_code_hash(text)
            OWNER TO insumap_definer;
        ALTER FUNCTION iam.lookup_refresh_token(text) OWNER TO insumap_definer;
        ALTER FUNCTION clinic.current_patient_id() OWNER TO insumap_definer;
        ALTER FUNCTION clinic.on_clinic_suspended() OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA iam, clinic FROM insumap_definer;

        REVOKE EXECUTE ON FUNCTION
            iam.lookup_login_identity(text),
            iam.lookup_invitation_by_code_hash(text),
            iam.lookup_refresh_token(text),
            clinic.current_patient_id(),
            clinic.on_clinic_suspended()
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            iam.lookup_login_identity(text),
            iam.lookup_invitation_by_code_hash(text),
            iam.lookup_refresh_token(text),
            clinic.current_patient_id()
            TO insumap_app;
        GRANT EXECUTE ON FUNCTION clinic.current_patient_id() TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the definer functions, their policies and privileges."""
    op.execute(
        """
        DROP TRIGGER tg_clinics_on_suspended ON clinic.clinics;
        DROP FUNCTION clinic.on_clinic_suspended();
        DROP FUNCTION clinic.current_patient_id();
        DROP FUNCTION iam.lookup_refresh_token(text);
        DROP FUNCTION iam.lookup_invitation_by_code_hash(text);
        DROP FUNCTION iam.lookup_login_identity(text);

        DROP POLICY patients_definer_select ON clinic.patients;
        DROP POLICY clinics_definer_select ON clinic.clinics;
        DROP POLICY refresh_tokens_definer_update ON iam.refresh_tokens;
        DROP POLICY refresh_tokens_definer_select ON iam.refresh_tokens;
        DROP POLICY credential_invitations_definer_select
            ON iam.credential_invitations;
        DROP POLICY memberships_definer_select ON iam.memberships;
        DROP POLICY users_definer_select ON iam.users;

        REVOKE UPDATE (revoked_at, revoked_reason) ON iam.refresh_tokens
            FROM insumap_definer;
        REVOKE SELECT ON iam.users, iam.memberships, iam.roles,
            iam.credential_invitations, iam.refresh_tokens,
            clinic.clinics, clinic.patients
            FROM insumap_definer;
        """
    )
