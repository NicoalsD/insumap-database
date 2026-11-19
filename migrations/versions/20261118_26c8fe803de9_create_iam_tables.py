"""Create the iam tables (Modelo de datos 9.1, Diccionario section 4).

Tables: roles, permissions, role_permissions, users, memberships,
credential_invitations, auth_challenges, refresh_tokens, mfa_factors,
login_attempts, login_lockouts, password_history, security_events and
rate_limit_buckets.

Columns that reference clinic.clinics or clinic.patients are created here
without their foreign key; the clinic revision adds those constraints once the
referenced tables exist.

Secrets are stored only as hashes or ciphertext (Seguridad 8.2). Lengths that
mirror FieldLimits: EMAIL_MAX_LENGTH = 254, REQUEST_ID_MAX_LENGTH = 64 and the
proposed REASON_MIN_LENGTH = 10 and REASON_MAX_LENGTH = 500.

RLS policies and grants come in a later revision.

Revision ID: 26c8fe803de9
Revises: 9bd786a0725c
Create Date: 2026-11-18 10:20:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "26c8fe803de9"
down_revision: str | None = "9bd786a0725c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the iam tables, their indexes and their triggers."""
    # RBAC catalogs (RLS-0), filled by the seed revision.
    op.execute(
        """
        CREATE TABLE iam.roles (
            id uuid NOT NULL DEFAULT uuidv7(),
            code varchar(32) NOT NULL,
            name_es varchar(80) NOT NULL,
            description varchar(300) NOT NULL,
            requires_mfa boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_roles PRIMARY KEY (id),
            CONSTRAINT uq_roles_code UNIQUE (code),
            CONSTRAINT ck_roles_code CHECK (
                code IN ('PLATFORM_ADMIN', 'CLINIC_ADMIN', 'DOCTOR', 'PATIENT')
            )
        );
        COMMENT ON TABLE iam.roles IS 'RBAC role catalog (seed)';

        CREATE TABLE iam.permissions (
            id uuid NOT NULL DEFAULT uuidv7(),
            code varchar(64) NOT NULL,
            description varchar(300) NOT NULL,
            module varchar(3) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_permissions PRIMARY KEY (id),
            CONSTRAINT uq_permissions_code UNIQUE (code),
            CONSTRAINT ck_permissions_code CHECK (
                code ~ '^[a-z][a-z_]*(\\.[a-z][a-z_]*)+$'
            ),
            CONSTRAINT ck_permissions_module CHECK (module ~ '^M(1[0-3]|[1-9])$')
        );
        COMMENT ON TABLE iam.permissions IS 'Permission code catalog (seed)';

        CREATE TABLE iam.role_permissions (
            role_id uuid NOT NULL,
            permission_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_role_permissions PRIMARY KEY (role_id, permission_id),
            CONSTRAINT fk_role_permissions_role_id FOREIGN KEY (role_id)
                REFERENCES iam.roles (id) ON DELETE RESTRICT,
            CONSTRAINT fk_role_permissions_permission_id FOREIGN KEY (permission_id)
                REFERENCES iam.permissions (id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_role_permissions_permission_id
            ON iam.role_permissions (permission_id);
        COMMENT ON TABLE iam.role_permissions IS
            'Role by permission matrix: a row grants the permission';
        """
    )

    # Accounts and memberships.
    op.execute(
        """
        CREATE TABLE iam.users (
            id uuid NOT NULL DEFAULT uuidv7(),
            email varchar(254),
            email_normalized varchar(254),
            email_verified_at timestamptz,
            password_hash varchar(255),
            password_changed_at timestamptz,
            token_version integer NOT NULL DEFAULT 0,
            status varchar(16) NOT NULL DEFAULT 'IMPORTED',
            status_reason varchar(500),
            status_changed_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz,
            is_system boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_users PRIMARY KEY (id),
            CONSTRAINT ck_users_email_pair CHECK (
                (email IS NULL) = (email_normalized IS NULL)
            ),
            CONSTRAINT ck_users_token_version CHECK (token_version >= 0),
            CONSTRAINT ck_users_status CHECK (
                status IN ('IMPORTED', 'INVITED', 'ACTIVE', 'SUSPENDED', 'DEACTIVATED')
            ),
            CONSTRAINT ck_users_status_reason_length CHECK (
                char_length(status_reason) BETWEEN 10 AND 500
            ),
            CONSTRAINT ck_users_status_reason_required CHECK (
                status NOT IN ('SUSPENDED', 'DEACTIVATED') OR status_reason IS NOT NULL
            ),
            CONSTRAINT ck_users_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_users_email_normalized ON iam.users (email_normalized)
            WHERE email_normalized IS NOT NULL;
        CREATE INDEX ix_users_status ON iam.users (status);
        COMMENT ON TABLE iam.users IS
            'Identity and credentials of every person with an account';
        COMMENT ON COLUMN iam.users.password_hash IS
            'Argon2id hash in PHC format. Secret (class S)';
        COMMENT ON COLUMN iam.users.token_version IS
            'tv claim of the JWT; incremented to cut every access token';

        CREATE TABLE iam.memberships (
            id uuid NOT NULL DEFAULT uuidv7(),
            user_id uuid NOT NULL,
            clinic_id uuid,
            role_id uuid NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            ended_at timestamptz,
            ended_reason varchar(500),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_memberships PRIMARY KEY (id),
            CONSTRAINT uq_memberships_user_id UNIQUE (user_id),
            CONSTRAINT fk_memberships_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_memberships_role_id FOREIGN KEY (role_id)
                REFERENCES iam.roles (id) ON DELETE RESTRICT,
            CONSTRAINT ck_memberships_ended CHECK ((ended_at IS NULL) = is_active),
            CONSTRAINT ck_memberships_ended_reason_length CHECK (
                char_length(ended_reason) BETWEEN 10 AND 500
            )
        );
        CREATE INDEX ix_memberships_clinic_id_role_id
            ON iam.memberships (clinic_id, role_id);
        COMMENT ON TABLE iam.memberships IS
            'Link of a person with a clinic and a role (one per user in the MVP)';

        CREATE FUNCTION iam.check_membership_clinic_role() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $$
            DECLARE
                role_code varchar;
            BEGIN
                SELECT r.code INTO role_code
                  FROM iam.roles AS r
                 WHERE r.id = NEW.role_id;
                IF (NEW.clinic_id IS NULL) <> (role_code = 'PLATFORM_ADMIN') THEN
                    RAISE EXCEPTION
                        'membership clinic_id must be NULL only for PLATFORM_ADMIN'
                        USING ERRCODE = 'check_violation',
                              CONSTRAINT = 'ck_memberships_clinic_role';
                END IF;
                RETURN NEW;
            END
            $$;
        CREATE TRIGGER tg_memberships_check_clinic_role
            BEFORE INSERT OR UPDATE OF clinic_id, role_id ON iam.memberships
            FOR EACH ROW EXECUTE FUNCTION iam.check_membership_clinic_role();
        """
    )

    # Invitations and short-lived challenges.
    op.execute(
        """
        CREATE TABLE iam.credential_invitations (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            user_id uuid NOT NULL,
            patient_id uuid,
            purpose varchar(20) NOT NULL,
            channel varchar(5) NOT NULL,
            status varchar(8) NOT NULL DEFAULT 'PENDING',
            code_hash char(64) NOT NULL,
            code_key_id char(16) NOT NULL,
            failed_attempts smallint NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            issued_by uuid NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT now(),
            verified_at timestamptz,
            used_at timestamptz,
            revoked_at timestamptz,
            revoked_by uuid,
            replaced_by_id uuid,
            delivery_status varchar(14) NOT NULL DEFAULT 'NOT_APPLICABLE',
            delivery_error_code varchar(40),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_credential_invitations PRIMARY KEY (id),
            CONSTRAINT fk_credential_invitations_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_credential_invitations_issued_by FOREIGN KEY (issued_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_credential_invitations_revoked_by FOREIGN KEY (revoked_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_credential_invitations_replaced_by_id
                FOREIGN KEY (replaced_by_id)
                REFERENCES iam.credential_invitations (id) ON DELETE RESTRICT,
            CONSTRAINT ck_credential_invitations_clinic CHECK (
                clinic_id IS NOT NULL OR purpose = 'STAFF_ACTIVATION'
            ),
            CONSTRAINT ck_credential_invitations_purpose CHECK (
                purpose IN ('PATIENT_ACTIVATION', 'STAFF_ACTIVATION', 'PASSWORD_RESET')
            ),
            CONSTRAINT ck_credential_invitations_channel CHECK (
                channel IN ('PRINT', 'EMAIL')
            ),
            CONSTRAINT ck_credential_invitations_status CHECK (
                status IN (
                    'PENDING', 'VERIFIED', 'USED', 'EXPIRED', 'REVOKED', 'LOCKED'
                )
            ),
            CONSTRAINT ck_credential_invitations_failed_attempts CHECK (
                failed_attempts >= 0
            ),
            CONSTRAINT ck_credential_invitations_expiry CHECK (expires_at > issued_at),
            CONSTRAINT ck_credential_invitations_delivery_status CHECK (
                delivery_status IN ('NOT_APPLICABLE', 'PENDING', 'SENT', 'FAILED')
            )
        );
        CREATE UNIQUE INDEX ux_credential_invitations_code_hash
            ON iam.credential_invitations (code_hash);
        CREATE UNIQUE INDEX ux_credential_invitations_open
            ON iam.credential_invitations (user_id, purpose)
            WHERE status IN ('PENDING', 'VERIFIED');
        CREATE INDEX ix_credential_invitations_expiry
            ON iam.credential_invitations (expires_at)
            WHERE status IN ('PENDING', 'VERIFIED');
        CREATE INDEX ix_credential_invitations_patient_id
            ON iam.credential_invitations (patient_id, issued_at DESC);
        COMMENT ON TABLE iam.credential_invitations IS
            'Single-use invitations; the code is stored only as an HMAC';
        COMMENT ON COLUMN iam.credential_invitations.code_hash IS
            'HMAC-SHA256 of the normalized activation code. Secret (class S)';

        CREATE TABLE iam.auth_challenges (
            id uuid NOT NULL DEFAULT uuidv7(),
            purpose varchar(18) NOT NULL,
            token_hash char(64) NOT NULL,
            user_id uuid NOT NULL,
            clinic_id uuid,
            invitation_id uuid,
            attempts smallint NOT NULL DEFAULT 0,
            max_attempts smallint NOT NULL,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            ip_hmac char(64),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_auth_challenges PRIMARY KEY (id),
            CONSTRAINT fk_auth_challenges_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_auth_challenges_invitation_id FOREIGN KEY (invitation_id)
                REFERENCES iam.credential_invitations (id) ON DELETE RESTRICT,
            CONSTRAINT ck_auth_challenges_purpose CHECK (
                purpose IN ('ACTIVATION', 'MFA', 'PASSWORD_RESET', 'EMAIL_VERIFICATION')
            ),
            CONSTRAINT ck_auth_challenges_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_auth_challenges_max_attempts CHECK (max_attempts > 0)
        );
        CREATE UNIQUE INDEX ux_auth_challenges_token_hash
            ON iam.auth_challenges (token_hash);
        CREATE INDEX ix_auth_challenges_expires_at ON iam.auth_challenges (expires_at);
        COMMENT ON TABLE iam.auth_challenges IS
            'Short-lived single-use tokens (activation, MFA, reset, email); SHA-256';
        COMMENT ON COLUMN iam.auth_challenges.token_hash IS
            'SHA-256 of the 256-bit opaque token. Secret (class S)';
        """
    )

    # Sessions and second factor.
    op.execute(
        """
        CREATE TABLE iam.refresh_tokens (
            id uuid NOT NULL DEFAULT uuidv7(),
            user_id uuid NOT NULL,
            clinic_id uuid,
            family_id uuid NOT NULL,
            token_hash char(64) NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            family_expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            revoked_reason varchar(20),
            replaced_by_id uuid,
            ip_hmac char(64),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_refresh_tokens PRIMARY KEY (id),
            CONSTRAINT fk_refresh_tokens_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_refresh_tokens_replaced_by_id FOREIGN KEY (replaced_by_id)
                REFERENCES iam.refresh_tokens (id) ON DELETE RESTRICT,
            CONSTRAINT ck_refresh_tokens_revoked_reason CHECK (
                revoked_reason IN (
                    'ROTATED', 'LOGOUT', 'LOGOUT_ALL', 'PASSWORD_CHANGED',
                    'ROLE_CHANGED', 'ACCOUNT_SUSPENDED', 'ACCOUNT_DEACTIVATED',
                    'CLINIC_SUSPENDED', 'MFA_RESET', 'REUSE_DETECTED', 'ADMIN'
                )
            ),
            CONSTRAINT ck_refresh_tokens_revoked CHECK (
                (revoked_at IS NULL) = (revoked_reason IS NULL)
            )
        );
        CREATE UNIQUE INDEX ux_refresh_tokens_token_hash
            ON iam.refresh_tokens (token_hash);
        CREATE INDEX ix_refresh_tokens_family_id ON iam.refresh_tokens (family_id);
        CREATE INDEX ix_refresh_tokens_user_active ON iam.refresh_tokens (user_id)
            WHERE revoked_at IS NULL;
        COMMENT ON TABLE iam.refresh_tokens IS
            'Opaque refresh tokens with rotation and reuse detection; SHA-256';
        COMMENT ON COLUMN iam.refresh_tokens.family_id IS
            'Rotation family created on each login; travels as sid in the JWT';
        COMMENT ON COLUMN iam.refresh_tokens.token_hash IS
            'SHA-256 of the refresh token. Secret (class S)';

        CREATE TABLE iam.mfa_factors (
            id uuid NOT NULL DEFAULT uuidv7(),
            user_id uuid NOT NULL,
            factor_type varchar(13) NOT NULL,
            secret_ciphertext bytea,
            code_hash char(64),
            key_id char(16) NOT NULL,
            last_used_step bigint,
            confirmed_at timestamptz,
            used_at timestamptz,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_mfa_factors PRIMARY KEY (id),
            CONSTRAINT fk_mfa_factors_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_mfa_factors_factor_type CHECK (
                factor_type IN ('TOTP', 'RECOVERY_CODE')
            ),
            CONSTRAINT ck_mfa_factors_secret CHECK (
                (factor_type = 'TOTP') = (secret_ciphertext IS NOT NULL)
            ),
            CONSTRAINT ck_mfa_factors_code_hash CHECK (
                (factor_type = 'RECOVERY_CODE') = (code_hash IS NOT NULL)
            )
        );
        CREATE UNIQUE INDEX ux_mfa_factors_active_totp ON iam.mfa_factors (user_id)
            WHERE factor_type = 'TOTP' AND revoked_at IS NULL;
        CREATE UNIQUE INDEX ux_mfa_factors_code_hash ON iam.mfa_factors (code_hash)
            WHERE code_hash IS NOT NULL;
        CREATE INDEX ix_mfa_factors_user_id ON iam.mfa_factors (user_id);
        COMMENT ON TABLE iam.mfa_factors IS
            'Staff second factor: encrypted TOTP secret and one-time recovery codes';
        COMMENT ON COLUMN iam.mfa_factors.secret_ciphertext IS
            'TOTP secret encrypted with AES-256-GCM. Secret (class S)';
        COMMENT ON COLUMN iam.mfa_factors.code_hash IS
            'HMAC-SHA256 of a recovery code. Secret (class S)';

        CREATE TABLE iam.password_history (
            id uuid NOT NULL DEFAULT uuidv7(),
            user_id uuid NOT NULL,
            password_hash varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_password_history PRIMARY KEY (id),
            CONSTRAINT fk_password_history_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_password_history_user_id
            ON iam.password_history (user_id, created_at DESC);
        COMMENT ON TABLE iam.password_history IS
            'Previous password hashes to prevent reuse';
        COMMENT ON COLUMN iam.password_history.password_hash IS
            'Previous Argon2id hash. Secret (class S)';
        """
    )

    # Login throttling, rate limits and security events.
    op.execute(
        """
        CREATE TABLE iam.login_attempts (
            id uuid NOT NULL DEFAULT uuidv7(),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            user_id uuid,
            email_hmac char(64) NOT NULL,
            ip_hmac char(64) NOT NULL,
            outcome varchar(7) NOT NULL,
            reason varchar(20),
            request_id varchar(64),
            CONSTRAINT pk_login_attempts PRIMARY KEY (id),
            CONSTRAINT fk_login_attempts_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_login_attempts_outcome CHECK (
                outcome IN ('SUCCESS', 'FAILURE')
            ),
            CONSTRAINT ck_login_attempts_reason CHECK (
                reason IN (
                    'INVALID_CREDENTIALS', 'LOCKED', 'NOT_ACTIVE', 'RATE_LIMITED',
                    'MFA_FAILED'
                )
            ),
            CONSTRAINT ck_login_attempts_reason_outcome CHECK (
                (outcome = 'SUCCESS') = (reason IS NULL)
            )
        );
        CREATE INDEX ix_login_attempts_pair
            ON iam.login_attempts (email_hmac, ip_hmac, occurred_at DESC);
        CREATE INDEX ix_login_attempts_email
            ON iam.login_attempts (email_hmac, occurred_at DESC);
        CREATE INDEX ix_login_attempts_occurred_at ON iam.login_attempts (occurred_at);
        COMMENT ON TABLE iam.login_attempts IS
            'Every login attempt, with HMAC of email and IP only';

        CREATE TABLE iam.login_lockouts (
            id uuid NOT NULL DEFAULT uuidv7(),
            scope varchar(7) NOT NULL,
            key_hmac char(64) NOT NULL,
            level smallint NOT NULL DEFAULT 0,
            locked_until timestamptz,
            last_failure_at timestamptz,
            reset_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_login_lockouts PRIMARY KEY (id),
            CONSTRAINT ck_login_lockouts_scope CHECK (scope IN ('PAIR', 'ACCOUNT')),
            CONSTRAINT ck_login_lockouts_level CHECK (level >= 0)
        );
        CREATE UNIQUE INDEX ux_login_lockouts_scope_key
            ON iam.login_lockouts (scope, key_hmac);
        COMMENT ON TABLE iam.login_lockouts IS
            'Escalating lockout state per (email, IP) pair and per email';

        CREATE TABLE iam.rate_limit_buckets (
            family varchar(40) NOT NULL,
            key_hmac char(64) NOT NULL,
            window_start timestamptz NOT NULL,
            hits integer NOT NULL DEFAULT 1,
            limit_event_logged boolean NOT NULL DEFAULT false,
            CONSTRAINT pk_rate_limit_buckets
                PRIMARY KEY (family, key_hmac, window_start),
            CONSTRAINT ck_rate_limit_buckets_hits CHECK (hits > 0)
        );
        CREATE INDEX ix_rate_limit_buckets_window_start
            ON iam.rate_limit_buckets (window_start);
        COMMENT ON TABLE iam.rate_limit_buckets IS
            'Fixed-window counters shared by every API instance';

        CREATE TABLE iam.security_events (
            id uuid NOT NULL DEFAULT uuidv7(),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            event_type varchar(40) NOT NULL,
            severity varchar(8) NOT NULL,
            user_id uuid,
            clinic_id uuid,
            ip_truncated cidr,
            ip_hmac char(64),
            request_id varchar(64),
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT pk_security_events PRIMARY KEY (id),
            CONSTRAINT fk_security_events_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_security_events_event_type CHECK (
                event_type IN (
                    'AUDIT_CHAIN_BROKEN', 'REFRESH_TOKEN_REUSE_DETECTED',
                    'ACCOUNT_LOCKED', 'MFA_RESET', 'LLM_DATA_POLICY_BLOCKED',
                    'MFA_FAILED', 'MFA_RECOVERY_CODE_USED', 'PERMISSION_DENIED',
                    'PHI_ACCESS_DENIED', 'ACTIVATION_LOCKED', 'PUSH_ENDPOINT_REJECTED',
                    'CSRF_CHECK_FAILED', 'LOGIN_FAILED', 'ACCOUNT_UNLOCKED',
                    'ACTIVATION_FAILED', 'ACCOUNT_ACTIVATED', 'ROLE_CHANGED',
                    'RATE_LIMITED', 'PASSWORD_CHANGED', 'PASSWORD_RESET_REQUESTED',
                    'SESSIONS_REVOKED', 'IMPORT_REJECTED', 'SECRET_ROTATED'
                )
            ),
            CONSTRAINT ck_security_events_severity CHECK (
                severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
            ),
            CONSTRAINT ck_security_events_details CHECK (
                jsonb_typeof(details) = 'object'
            )
        );
        CREATE INDEX ix_security_events_clinic_occurred
            ON iam.security_events (clinic_id, occurred_at DESC);
        CREATE INDEX ix_security_events_severity_occurred
            ON iam.security_events (severity, occurred_at DESC);
        CREATE INDEX ix_security_events_user_id
            ON iam.security_events (user_id, occurred_at DESC);
        CREATE TRIGGER tg_security_events_append_only
            BEFORE UPDATE OR DELETE ON iam.security_events
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
        COMMENT ON TABLE iam.security_events IS
            'Append-only security events; clinic_id NULL before the context exists';
        """
    )


def downgrade() -> None:
    """Drop the iam tables in reverse dependency order."""
    op.execute(
        """
        DROP TABLE iam.security_events;
        DROP TABLE iam.rate_limit_buckets;
        DROP TABLE iam.login_lockouts;
        DROP TABLE iam.login_attempts;
        DROP TABLE iam.password_history;
        DROP TABLE iam.mfa_factors;
        DROP TABLE iam.refresh_tokens;
        DROP TABLE iam.auth_challenges;
        DROP TABLE iam.credential_invitations;
        DROP TABLE iam.memberships;
        DROP FUNCTION iam.check_membership_clinic_role();
        DROP TABLE iam.users;
        DROP TABLE iam.role_permissions;
        DROP TABLE iam.permissions;
        DROP TABLE iam.roles;
        """
    )
