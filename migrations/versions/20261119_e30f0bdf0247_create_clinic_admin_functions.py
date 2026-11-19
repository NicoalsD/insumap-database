"""Create the SECURITY DEFINER functions that manage the clinic administrators.

API 8.3 (POST /clinics/{clinic_id}/admins and .../reset-access) and Modelo de
datos 4.4, point 6. A PLATFORM_ADMIN has no clinic context, so it cannot write
the clinic's users, memberships, profiles and invitations under RLS. Two
functions do it, after checking the role in the body:

- iam.create_first_clinic_admin(...): creates the account (INVITED), the
  CLINIC_ADMIN membership, the staff profile and a STAFF_ACTIVATION invitation
  sent by e-mail.
- iam.reset_clinic_admin_access(...): revokes the open invitations, the MFA
  factors and the refresh tokens of a CLINIC_ADMIN of the clinic, bumps
  token_version, sets the account back to INVITED and issues a new invitation.

Neither writes audit.audit_log: the API's command audit does it. Both are owned
by insumap_definer, fix search_path to pg_catalog and take the code as an HMAC
(never the code itself). Because FORCE ROW LEVEL SECURITY also binds the
definer, the tables they write get permissive policies TO insumap_definer; the
filter (role and clinic) is in the functions.

Revision ID: e30f0bdf0247
Revises: b7d41c2e9a10
Create Date: 2026-11-19 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e30f0bdf0247"
down_revision: str | None = "b7d41c2e9a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREATE_SIGNATURE = "(uuid, varchar, varchar, char, char, timestamptz)"
RESET_SIGNATURE = "(uuid, uuid, char, char, timestamptz)"


def upgrade() -> None:
    """Grant insumap_definer the writes and create the two functions."""
    # Writes and reads that the definer needs; no secret column is exposed.
    op.execute(
        """
        GRANT INSERT ON iam.users, iam.memberships, iam.credential_invitations,
            clinic.staff_profiles
            TO insumap_definer;
        GRANT UPDATE (status, status_changed_at, updated_at, token_version,
            lock_version) ON iam.users TO insumap_definer;
        GRANT UPDATE (status, revoked_at, revoked_by)
            ON iam.credential_invitations TO insumap_definer;
        GRANT SELECT (id, user_id, revoked_at), UPDATE (revoked_at)
            ON iam.mfa_factors TO insumap_definer;

        CREATE POLICY users_definer_insert ON iam.users
            FOR INSERT TO insumap_definer WITH CHECK (true);
        CREATE POLICY users_definer_update ON iam.users
            FOR UPDATE TO insumap_definer USING (true) WITH CHECK (true);
        CREATE POLICY memberships_definer_insert ON iam.memberships
            FOR INSERT TO insumap_definer WITH CHECK (true);
        CREATE POLICY credential_invitations_definer_insert
            ON iam.credential_invitations
            FOR INSERT TO insumap_definer WITH CHECK (true);
        CREATE POLICY credential_invitations_definer_update
            ON iam.credential_invitations
            FOR UPDATE TO insumap_definer USING (true) WITH CHECK (true);
        CREATE POLICY staff_profiles_definer_insert ON clinic.staff_profiles
            FOR INSERT TO insumap_definer WITH CHECK (true);
        CREATE POLICY mfa_factors_definer_select ON iam.mfa_factors
            FOR SELECT TO insumap_definer USING (true);
        CREATE POLICY mfa_factors_definer_update ON iam.mfa_factors
            FOR UPDATE TO insumap_definer USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE FUNCTION iam.create_first_clinic_admin(
            p_clinic_id uuid,
            p_email varchar,
            p_full_name varchar,
            p_code_hash char(64),
            p_code_key_id char(16),
            p_expires_at timestamptz
        ) RETURNS TABLE (user_id uuid, invitation_id uuid)
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_actor uuid := iam.app_user_id();
                v_status varchar;
                v_user_id uuid;
                v_invitation_id uuid;
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'PLATFORM_ADMIN' THEN
                    RAISE EXCEPTION
                        'only PLATFORM_ADMIN can create the first CLINIC_ADMIN';
                END IF;
                IF v_actor IS NULL THEN
                    RAISE EXCEPTION 'an authenticated user is required';
                END IF;
                IF p_code_hash IS NULL OR p_code_hash !~ '^[0-9a-f]{64}$' THEN
                    RAISE EXCEPTION 'code hash must be a SHA-256 hex digest';
                END IF;
                IF p_email IS NULL OR btrim(p_email) = '' THEN
                    RAISE EXCEPTION 'email is required';
                END IF;

                SELECT c.status INTO v_status
                  FROM clinic.clinics AS c
                 WHERE c.id = p_clinic_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'clinic not found';
                END IF;
                IF v_status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'clinic is not ACTIVE';
                END IF;

                -- A duplicate e-mail raises unique_violation as is (API: 409).
                INSERT INTO iam.users (email, email_normalized, status)
                VALUES (btrim(p_email), lower(btrim(p_email)), 'INVITED')
                RETURNING id INTO v_user_id;

                INSERT INTO iam.memberships (user_id, clinic_id, role_id)
                SELECT v_user_id, p_clinic_id, r.id
                  FROM iam.roles AS r
                 WHERE r.code = 'CLINIC_ADMIN';

                INSERT INTO clinic.staff_profiles (user_id, clinic_id, full_name)
                VALUES (v_user_id, p_clinic_id, p_full_name);

                INSERT INTO iam.credential_invitations (
                    clinic_id, user_id, purpose, channel, delivery_status,
                    code_hash, code_key_id, expires_at, issued_by
                ) VALUES (
                    p_clinic_id, v_user_id, 'STAFF_ACTIVATION', 'EMAIL', 'PENDING',
                    p_code_hash, p_code_key_id, p_expires_at, v_actor
                )
                RETURNING id INTO v_invitation_id;

                RETURN QUERY SELECT v_user_id, v_invitation_id;
            END
            $$;
        COMMENT ON FUNCTION iam.create_first_clinic_admin(
            uuid, varchar, varchar, char, char, timestamptz
        ) IS 'First CLINIC_ADMIN of a clinic, created by PLATFORM_ADMIN (API 8.3)';

        CREATE FUNCTION iam.reset_clinic_admin_access(
            p_clinic_id uuid,
            p_user_id uuid,
            p_code_hash char(64),
            p_code_key_id char(16),
            p_expires_at timestamptz
        ) RETURNS uuid
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_actor uuid := iam.app_user_id();
                v_status varchar;
                v_invitation_id uuid;
            BEGIN
                IF iam.app_role() IS DISTINCT FROM 'PLATFORM_ADMIN' THEN
                    RAISE EXCEPTION
                        'only PLATFORM_ADMIN can reset a CLINIC_ADMIN';
                END IF;
                IF v_actor IS NULL THEN
                    RAISE EXCEPTION 'an authenticated user is required';
                END IF;
                IF p_code_hash IS NULL OR p_code_hash !~ '^[0-9a-f]{64}$' THEN
                    RAISE EXCEPTION 'code hash must be a SHA-256 hex digest';
                END IF;

                PERFORM 1
                  FROM iam.memberships AS m
                  JOIN iam.roles AS r ON r.id = m.role_id
                 WHERE m.user_id = p_user_id
                   AND m.clinic_id = p_clinic_id
                   AND m.is_active
                   AND r.code = 'CLINIC_ADMIN';
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'not found';
                END IF;

                -- Locks the account so that two resets cannot interleave.
                SELECT u.status INTO v_status
                  FROM iam.users AS u
                 WHERE u.id = p_user_id
                   FOR UPDATE;
                -- Reactivating a suspended or deactivated account is not a reset.
                IF v_status NOT IN ('ACTIVE', 'INVITED') THEN
                    RAISE EXCEPTION 'user status % cannot be reset', v_status;
                END IF;

                -- Open invitations go first: the unique index allows one open
                -- STAFF_ACTIVATION per user.
                UPDATE iam.credential_invitations AS ci
                   SET status = 'REVOKED',
                       revoked_at = now(),
                       revoked_by = v_actor
                 WHERE ci.user_id = p_user_id
                   AND ci.status IN ('PENDING', 'VERIFIED');

                UPDATE iam.mfa_factors AS f
                   SET revoked_at = now()
                 WHERE f.user_id = p_user_id
                   AND f.revoked_at IS NULL;

                UPDATE iam.refresh_tokens AS t
                   SET revoked_at = now(),
                       revoked_reason = 'MFA_RESET'
                 WHERE t.user_id = p_user_id
                   AND t.revoked_at IS NULL;

                UPDATE iam.users AS u
                   SET status = 'INVITED',
                       status_changed_at = now(),
                       updated_at = now(),
                       token_version = u.token_version + 1,
                       lock_version = u.lock_version + 1
                 WHERE u.id = p_user_id;

                INSERT INTO iam.credential_invitations (
                    clinic_id, user_id, purpose, channel, delivery_status,
                    code_hash, code_key_id, expires_at, issued_by
                ) VALUES (
                    p_clinic_id, p_user_id, 'STAFF_ACTIVATION', 'EMAIL', 'PENDING',
                    p_code_hash, p_code_key_id, p_expires_at, v_actor
                )
                RETURNING id INTO v_invitation_id;

                RETURN v_invitation_id;
            END
            $$;
        COMMENT ON FUNCTION iam.reset_clinic_admin_access(
            uuid, uuid, char, char, timestamptz
        ) IS 'Resets a CLINIC_ADMIN: revokes sessions and MFA, new invitation';
        """
    )

    # Hand the functions to insumap_definer; it needs CREATE on the schema only
    # while the ownership changes (as in the earlier definer migrations).
    op.execute(
        f"""
        GRANT CREATE ON SCHEMA iam TO insumap_definer;
        ALTER FUNCTION iam.create_first_clinic_admin{CREATE_SIGNATURE}
            OWNER TO insumap_definer;
        ALTER FUNCTION iam.reset_clinic_admin_access{RESET_SIGNATURE}
            OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA iam FROM insumap_definer;

        REVOKE EXECUTE ON FUNCTION
            iam.create_first_clinic_admin{CREATE_SIGNATURE},
            iam.reset_clinic_admin_access{RESET_SIGNATURE}
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            iam.create_first_clinic_admin{CREATE_SIGNATURE},
            iam.reset_clinic_admin_access{RESET_SIGNATURE}
            TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the two functions, the definer policies and the definer grants."""
    op.execute(
        f"""
        DROP FUNCTION iam.reset_clinic_admin_access{RESET_SIGNATURE};
        DROP FUNCTION iam.create_first_clinic_admin{CREATE_SIGNATURE};

        DROP POLICY mfa_factors_definer_update ON iam.mfa_factors;
        DROP POLICY mfa_factors_definer_select ON iam.mfa_factors;
        DROP POLICY staff_profiles_definer_insert ON clinic.staff_profiles;
        DROP POLICY credential_invitations_definer_update
            ON iam.credential_invitations;
        DROP POLICY credential_invitations_definer_insert
            ON iam.credential_invitations;
        DROP POLICY memberships_definer_insert ON iam.memberships;
        DROP POLICY users_definer_update ON iam.users;
        DROP POLICY users_definer_insert ON iam.users;

        REVOKE SELECT (id, user_id, revoked_at), UPDATE (revoked_at)
            ON iam.mfa_factors FROM insumap_definer;
        REVOKE UPDATE (status, revoked_at, revoked_by)
            ON iam.credential_invitations FROM insumap_definer;
        REVOKE UPDATE (status, status_changed_at, updated_at, token_version,
            lock_version) ON iam.users FROM insumap_definer;
        REVOKE INSERT ON iam.users, iam.memberships, iam.credential_invitations,
            clinic.staff_profiles
            FROM insumap_definer;
        """
    )
