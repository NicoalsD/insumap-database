"""Enable RLS on the iam and clinic tables and grant the minimum privileges.

Policy templates (Modelo de datos 4.3):

- RLS-0 catalogs (roles, permissions, role_permissions): no RLS, SELECT only.
- RLS-4 before the context (auth_challenges, login_attempts, login_lockouts,
  rate_limit_buckets): no RLS, high-entropy keys or HMAC only, column grants.
  auth_challenges has a clinic_id, but it is informative and the row is found
  by a 256-bit token before any clinic is known.
- Every other table: ENABLE and FORCE ROW LEVEL SECURITY. Without app.clinic_id
  or app.user_id the policies return no rows (fail closed).

Own-row branches (user_id = iam.app_user_id()) do not require app.role, so
they also work with the provisional context that login and activation set
after a definer lookup (Seguridad 3.4, step 1.4).

The doctor-patient assignment is not checked here: PatientRecordAccessProxy
does it (Modelo de datos 4.4, point 2).

insumap_app has DML only, no DELETE except on password_history, and no UPDATE
or DELETE on append-only tables. insumap_readonly has SELECT without secret
(class S) columns and without the RLS-4 tables, refresh tokens, MFA factors or
password history.

Revision ID: 5a8641b04af7
Revises: 6d89797fe0f3
Create Date: 2026-11-18 10:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5a8641b04af7"
down_revision: str | None = "6d89797fe0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "iam.users",
    "iam.memberships",
    "iam.credential_invitations",
    "iam.refresh_tokens",
    "iam.mfa_factors",
    "iam.password_history",
    "iam.security_events",
    "clinic.clinics",
    "clinic.import_batches",
    "clinic.staff_profiles",
    "clinic.patients",
    "clinic.privacy_notice_versions",
    "clinic.patient_consents",
)

# (policy, table) pairs created by this revision, dropped in reverse order.
POLICIES = (
    ("users_select", "iam.users"),
    ("users_insert_clinic_admin", "iam.users"),
    ("users_update_own", "iam.users"),
    ("users_update_clinic_admin", "iam.users"),
    ("memberships_select", "iam.memberships"),
    ("memberships_insert_clinic_admin", "iam.memberships"),
    ("memberships_update_clinic_admin", "iam.memberships"),
    ("credential_invitations_select_clinic", "iam.credential_invitations"),
    ("credential_invitations_insert_clinic_admin", "iam.credential_invitations"),
    ("credential_invitations_update_clinic", "iam.credential_invitations"),
    ("credential_invitations_select_own", "iam.credential_invitations"),
    ("credential_invitations_update_own", "iam.credential_invitations"),
    ("refresh_tokens_select_own", "iam.refresh_tokens"),
    ("refresh_tokens_insert_own", "iam.refresh_tokens"),
    ("refresh_tokens_update_own", "iam.refresh_tokens"),
    ("refresh_tokens_select_clinic_admin", "iam.refresh_tokens"),
    ("refresh_tokens_update_clinic_admin", "iam.refresh_tokens"),
    ("mfa_factors_select_own", "iam.mfa_factors"),
    ("mfa_factors_insert_own", "iam.mfa_factors"),
    ("mfa_factors_update_own", "iam.mfa_factors"),
    ("mfa_factors_select_clinic_admin", "iam.mfa_factors"),
    ("mfa_factors_update_clinic_admin", "iam.mfa_factors"),
    ("password_history_select_own", "iam.password_history"),
    ("password_history_insert_own", "iam.password_history"),
    ("password_history_delete_own", "iam.password_history"),
    ("security_events_insert", "iam.security_events"),
    ("security_events_select", "iam.security_events"),
    ("clinics_select", "clinic.clinics"),
    ("clinics_insert_platform_admin", "clinic.clinics"),
    ("clinics_update_platform_admin", "clinic.clinics"),
    ("import_batches_select_clinic_admin", "clinic.import_batches"),
    ("import_batches_insert_clinic_admin", "clinic.import_batches"),
    ("import_batches_update_clinic_admin", "clinic.import_batches"),
    ("staff_profiles_select", "clinic.staff_profiles"),
    ("staff_profiles_insert_clinic_admin", "clinic.staff_profiles"),
    ("staff_profiles_update", "clinic.staff_profiles"),
    ("patients_select", "clinic.patients"),
    ("patients_insert_clinic_admin", "clinic.patients"),
    ("patients_update_clinic_admin", "clinic.patients"),
    ("privacy_notice_versions_select", "clinic.privacy_notice_versions"),
    ("privacy_notice_versions_insert_clinic_admin", "clinic.privacy_notice_versions"),
    ("privacy_notice_versions_update_clinic_admin", "clinic.privacy_notice_versions"),
    ("patient_consents_select", "clinic.patient_consents"),
    ("patient_consents_insert_own", "clinic.patient_consents"),
)


def upgrade() -> None:
    """Enable RLS, create the policies and grant the privileges."""
    op.execute(
        "\n".join(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
            for table in RLS_TABLES
        )
    )

    # iam.users (RLS-6): own row, or staff of the session clinic for CA, DR, SY.
    # iam.memberships (RLS-1 with a global branch): PLATFORM_ADMIN only sees its
    # own row; the trigger keeps clinic_id NULL for PLATFORM_ADMIN only.
    op.execute(
        """
        CREATE POLICY users_select ON iam.users FOR SELECT
            USING (
                id = iam.app_user_id()
                OR (
                    iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                    AND EXISTS (
                        SELECT 1 FROM iam.memberships AS m
                         WHERE m.user_id = users.id
                           AND m.clinic_id = iam.app_clinic_id()
                    )
                )
            );
        CREATE POLICY users_insert_clinic_admin ON iam.users FOR INSERT
            WITH CHECK (
                iam.app_role() = 'CLINIC_ADMIN' AND iam.app_clinic_id() IS NOT NULL
            );
        CREATE POLICY users_update_own ON iam.users FOR UPDATE
            USING (id = iam.app_user_id())
            WITH CHECK (id = iam.app_user_id());
        CREATE POLICY users_update_clinic_admin ON iam.users FOR UPDATE
            USING (
                iam.app_role() = 'CLINIC_ADMIN'
                AND EXISTS (
                    SELECT 1 FROM iam.memberships AS m
                     WHERE m.user_id = users.id
                       AND m.clinic_id = iam.app_clinic_id()
                )
            )
            WITH CHECK (iam.app_role() = 'CLINIC_ADMIN');

        CREATE POLICY memberships_select ON iam.memberships FOR SELECT
            USING (
                user_id = iam.app_user_id()
                OR (
                    clinic_id = iam.app_clinic_id()
                    AND iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                )
            );
        CREATE POLICY memberships_insert_clinic_admin ON iam.memberships
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY memberships_update_clinic_admin ON iam.memberships
            FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        """
    )

    # iam.credential_invitations (RLS-1): CA issues and revokes, SY expires.
    # The own branch lets the activation (patient, staff or PLATFORM_ADMIN with
    # clinic_id NULL) read and update its invitation after the definer lookup.
    op.execute(
        """
        CREATE POLICY credential_invitations_select_clinic
            ON iam.credential_invitations FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
            );
        CREATE POLICY credential_invitations_insert_clinic_admin
            ON iam.credential_invitations FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY credential_invitations_update_clinic
            ON iam.credential_invitations FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
            );
        CREATE POLICY credential_invitations_select_own
            ON iam.credential_invitations FOR SELECT
            USING (
                user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            );
        CREATE POLICY credential_invitations_update_own
            ON iam.credential_invitations FOR UPDATE
            USING (
                user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            )
            WITH CHECK (
                user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            );
        """
    )

    # iam.refresh_tokens, iam.mfa_factors, iam.password_history (RLS-6).
    op.execute(
        """
        CREATE POLICY refresh_tokens_select_own ON iam.refresh_tokens FOR SELECT
            USING (user_id = iam.app_user_id());
        CREATE POLICY refresh_tokens_insert_own ON iam.refresh_tokens FOR INSERT
            WITH CHECK (
                user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            );
        CREATE POLICY refresh_tokens_update_own ON iam.refresh_tokens FOR UPDATE
            USING (user_id = iam.app_user_id())
            WITH CHECK (user_id = iam.app_user_id());
        CREATE POLICY refresh_tokens_select_clinic_admin ON iam.refresh_tokens
            FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY refresh_tokens_update_clinic_admin ON iam.refresh_tokens
            FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY mfa_factors_select_own ON iam.mfa_factors FOR SELECT
            USING (user_id = iam.app_user_id());
        CREATE POLICY mfa_factors_insert_own ON iam.mfa_factors FOR INSERT
            WITH CHECK (user_id = iam.app_user_id());
        CREATE POLICY mfa_factors_update_own ON iam.mfa_factors FOR UPDATE
            USING (user_id = iam.app_user_id())
            WITH CHECK (user_id = iam.app_user_id());
        CREATE POLICY mfa_factors_select_clinic_admin ON iam.mfa_factors
            FOR SELECT
            USING (
                iam.app_role() = 'CLINIC_ADMIN'
                AND user_id <> iam.app_user_id()
                AND EXISTS (
                    SELECT 1 FROM iam.memberships AS m
                     WHERE m.user_id = mfa_factors.user_id
                       AND m.clinic_id = iam.app_clinic_id()
                )
            );
        CREATE POLICY mfa_factors_update_clinic_admin ON iam.mfa_factors
            FOR UPDATE
            USING (
                iam.app_role() = 'CLINIC_ADMIN'
                AND user_id <> iam.app_user_id()
                AND EXISTS (
                    SELECT 1 FROM iam.memberships AS m
                     WHERE m.user_id = mfa_factors.user_id
                       AND m.clinic_id = iam.app_clinic_id()
                )
            )
            WITH CHECK (
                iam.app_role() = 'CLINIC_ADMIN' AND user_id <> iam.app_user_id()
            );

        CREATE POLICY password_history_select_own ON iam.password_history
            FOR SELECT USING (user_id = iam.app_user_id());
        CREATE POLICY password_history_insert_own ON iam.password_history
            FOR INSERT WITH CHECK (user_id = iam.app_user_id());
        CREATE POLICY password_history_delete_own ON iam.password_history
            FOR DELETE USING (user_id = iam.app_user_id());
        """
    )

    # iam.security_events (RLS-7): insert in any context, read by role.
    op.execute(
        """
        CREATE POLICY security_events_insert ON iam.security_events FOR INSERT
            WITH CHECK (clinic_id IS NULL OR clinic_id = iam.app_clinic_id());
        CREATE POLICY security_events_select ON iam.security_events FOR SELECT
            USING (
                (iam.app_role() = 'CLINIC_ADMIN' AND clinic_id = iam.app_clinic_id())
                OR iam.app_role() = 'PLATFORM_ADMIN'
            );
        """
    )

    # clinic.clinics (RLS-8) and the clinic tables (RLS-1 and RLS-2).
    op.execute(
        """
        CREATE POLICY clinics_select ON clinic.clinics FOR SELECT
            USING (id = iam.app_clinic_id() OR iam.app_role() = 'PLATFORM_ADMIN');
        CREATE POLICY clinics_insert_platform_admin ON clinic.clinics FOR INSERT
            WITH CHECK (iam.app_role() = 'PLATFORM_ADMIN');
        CREATE POLICY clinics_update_platform_admin ON clinic.clinics FOR UPDATE
            USING (iam.app_role() = 'PLATFORM_ADMIN')
            WITH CHECK (iam.app_role() = 'PLATFORM_ADMIN');

        CREATE POLICY import_batches_select_clinic_admin ON clinic.import_batches
            FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY import_batches_insert_clinic_admin ON clinic.import_batches
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY import_batches_update_clinic_admin ON clinic.import_batches
            FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY staff_profiles_select ON clinic.staff_profiles FOR SELECT
            USING (
                (
                    clinic_id = iam.app_clinic_id()
                    AND iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                )
                OR user_id = iam.app_user_id()
            );
        CREATE POLICY staff_profiles_insert_clinic_admin ON clinic.staff_profiles
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY staff_profiles_update ON clinic.staff_profiles FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id()
                AND (iam.app_role() = 'CLINIC_ADMIN' OR user_id = iam.app_user_id())
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id()
                AND (iam.app_role() = 'CLINIC_ADMIN' OR user_id = iam.app_user_id())
            );

        CREATE POLICY patients_select ON clinic.patients FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND (
                    iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                    OR user_id = iam.app_user_id()
                )
            );
        CREATE POLICY patients_insert_clinic_admin ON clinic.patients FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY patients_update_clinic_admin ON clinic.patients FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY privacy_notice_versions_select
            ON clinic.privacy_notice_versions FOR SELECT
            USING (clinic_id = iam.app_clinic_id());
        CREATE POLICY privacy_notice_versions_insert_clinic_admin
            ON clinic.privacy_notice_versions FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY privacy_notice_versions_update_clinic_admin
            ON clinic.privacy_notice_versions FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY patient_consents_select ON clinic.patient_consents
            FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND (
                    iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                    OR patient_id = clinic.current_patient_id()
                )
            );
        CREATE POLICY patient_consents_insert_own ON clinic.patient_consents
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id()
                AND patient_id = clinic.current_patient_id()
            );
        """
    )

    # insumap_app: minimum DML per table.
    op.execute(
        """
        GRANT SELECT ON iam.roles, iam.permissions, iam.role_permissions
            TO insumap_app;
        GRANT SELECT, INSERT, UPDATE ON iam.users, iam.memberships,
            iam.credential_invitations, iam.refresh_tokens, iam.mfa_factors,
            iam.login_lockouts
            TO insumap_app;
        GRANT SELECT, INSERT ON iam.auth_challenges, iam.rate_limit_buckets
            TO insumap_app;
        GRANT UPDATE (attempts, consumed_at) ON iam.auth_challenges TO insumap_app;
        GRANT UPDATE (hits, limit_event_logged) ON iam.rate_limit_buckets
            TO insumap_app;
        GRANT SELECT, INSERT ON iam.login_attempts, iam.security_events
            TO insumap_app;
        GRANT SELECT, INSERT, DELETE ON iam.password_history TO insumap_app;

        GRANT SELECT, INSERT, UPDATE ON clinic.clinics, clinic.import_batches,
            clinic.staff_profiles, clinic.patients
            TO insumap_app;
        GRANT SELECT, INSERT ON clinic.privacy_notice_versions,
            clinic.patient_consents
            TO insumap_app;
        GRANT UPDATE (is_current) ON clinic.privacy_notice_versions TO insumap_app;
        """
    )

    # insumap_readonly: SELECT without class S columns.
    op.execute(
        """
        GRANT SELECT ON iam.roles, iam.permissions, iam.role_permissions,
            iam.memberships, iam.security_events,
            clinic.clinics, clinic.import_batches, clinic.staff_profiles,
            clinic.privacy_notice_versions, clinic.patient_consents
            TO insumap_readonly;
        GRANT SELECT (
            id, email, email_normalized, email_verified_at, password_changed_at,
            token_version, status, status_reason, status_changed_at,
            last_login_at, is_system, created_at, updated_at, lock_version
        ) ON iam.users TO insumap_readonly;
        GRANT SELECT (
            id, clinic_id, user_id, patient_id, purpose, channel, status,
            code_key_id, failed_attempts, expires_at, issued_by, issued_at,
            verified_at, used_at, revoked_at, revoked_by, replaced_by_id,
            delivery_status, delivery_error_code, created_at
        ) ON iam.credential_invitations TO insumap_readonly;
        GRANT SELECT (
            id, clinic_id, user_id, mrn, full_name, birth_date, sex,
            diabetes_type, diagnosis_date, contact_email, contact_phone,
            document_type, document_hash_key_id, document_key_id,
            document_last4, import_batch_id, created_at, updated_at,
            lock_version
        ) ON clinic.patients TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Revoke the privileges, drop the policies and disable RLS."""
    tables = ", ".join(RLS_TABLES)
    rls4 = (
        "iam.roles, iam.permissions, iam.role_permissions, iam.auth_challenges, "
        "iam.login_attempts, iam.login_lockouts, iam.rate_limit_buckets"
    )
    op.execute(
        f"""
        REVOKE ALL ON {tables}, {rls4} FROM insumap_app, insumap_readonly;
        """
    )
    op.execute(
        "\n".join(
            f"DROP POLICY {policy} ON {table};" for policy, table in reversed(POLICIES)
        )
    )
    op.execute(
        "\n".join(
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
            for table in RLS_TABLES
        )
    )
