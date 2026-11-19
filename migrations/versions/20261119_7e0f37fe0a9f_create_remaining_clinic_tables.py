"""Create the remaining clinic tables with their RLS policies and grants.

Modelo de datos 9.2, Diccionario section 5. This revision completes the clinic
schema: sites, staff_sites, care_team_assignments, privacy_requests,
import_rows and clinic_settings.

Row level security (Modelo de datos 4.3 and 4.5):

- sites, staff_sites (RLS-1): CLINIC_ADMIN, DOCTOR and SYSTEM read the session
  clinic; CLINIC_ADMIN writes.
- care_team_assignments (RLS-2): CLINIC_ADMIN and SYSTEM read the clinic, a
  DOCTOR reads only the rows where doctor_user_id is the session user, a
  PATIENT reads the rows of its own care team; CLINIC_ADMIN writes. The check
  that a doctor is assigned to a patient is done by PatientRecordAccessProxy
  (Modelo de datos 4.4, point 2), not by the clinical policies.
- privacy_requests (RLS-2): the PATIENT creates and reads its own, CLINIC_ADMIN
  and SYSTEM read the clinic and CLINIC_ADMIN processes them.
- import_rows (RLS-1): CLINIC_ADMIN only.
- clinic_settings (RLS-3): global rows (clinic_id NULL) and the session clinic
  are readable by the roles of a clinic; CLINIC_ADMIN writes its own clinic row;
  insumap_migrator seeds. The role and context requirements keep it fail
  closed: without them not even the global row is visible.

A care team always has exactly one primary doctor (RF-28): a deferred
constraint trigger checks it at commit.

Revision ID: 7e0f37fe0a9f
Revises: c98639dec5a8
Create Date: 2026-11-19 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "7e0f37fe0a9f"
down_revision: str | None = "c98639dec5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "clinic.sites",
    "clinic.staff_sites",
    "clinic.care_team_assignments",
    "clinic.privacy_requests",
    "clinic.import_rows",
    "clinic.clinic_settings",
)

POLICIES = (
    ("sites_select", "clinic.sites"),
    ("sites_insert_clinic_admin", "clinic.sites"),
    ("sites_update_clinic_admin", "clinic.sites"),
    ("staff_sites_select", "clinic.staff_sites"),
    ("staff_sites_insert_clinic_admin", "clinic.staff_sites"),
    ("staff_sites_delete_clinic_admin", "clinic.staff_sites"),
    ("care_team_assignments_select", "clinic.care_team_assignments"),
    ("care_team_assignments_insert_clinic_admin", "clinic.care_team_assignments"),
    ("care_team_assignments_update_clinic_admin", "clinic.care_team_assignments"),
    ("privacy_requests_select", "clinic.privacy_requests"),
    ("privacy_requests_insert_own", "clinic.privacy_requests"),
    ("privacy_requests_update_clinic_admin", "clinic.privacy_requests"),
    ("import_rows_select_clinic_admin", "clinic.import_rows"),
    ("import_rows_insert_clinic_admin", "clinic.import_rows"),
    ("clinic_settings_select", "clinic.clinic_settings"),
    ("clinic_settings_insert_clinic_admin", "clinic.clinic_settings"),
    ("clinic_settings_update_clinic_admin", "clinic.clinic_settings"),
    ("clinic_settings_migrator", "clinic.clinic_settings"),
)


def upgrade() -> None:
    """Create the tables, the primary doctor trigger, the policies and grants."""
    op.execute(
        """
        CREATE TABLE clinic.sites (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            name varchar(120) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_sites PRIMARY KEY (id),
            CONSTRAINT fk_sites_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_sites_clinic_name
            ON clinic.sites (clinic_id, lower(name));
        COMMENT ON TABLE clinic.sites IS 'Clinic sites (Post-MVP, RF-15)';

        CREATE TABLE clinic.staff_sites (
            user_id uuid NOT NULL,
            site_id uuid NOT NULL,
            clinic_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_staff_sites PRIMARY KEY (user_id, site_id),
            CONSTRAINT fk_staff_sites_user_id FOREIGN KEY (user_id)
                REFERENCES clinic.staff_profiles (user_id) ON DELETE RESTRICT,
            CONSTRAINT fk_staff_sites_site_id FOREIGN KEY (site_id)
                REFERENCES clinic.sites (id) ON DELETE RESTRICT,
            CONSTRAINT fk_staff_sites_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_staff_sites_site_id ON clinic.staff_sites (site_id);
        COMMENT ON TABLE clinic.staff_sites IS
            'Many to many link between staff and sites (Post-MVP, RF-15)';
        """
    )

    op.execute(
        """
        CREATE TABLE clinic.care_team_assignments (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            doctor_user_id uuid NOT NULL,
            is_primary boolean NOT NULL DEFAULT false,
            assigned_by uuid NOT NULL,
            assigned_at timestamptz NOT NULL DEFAULT now(),
            ended_at timestamptz,
            ended_by uuid,
            end_reason varchar(500),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_care_team_assignments PRIMARY KEY (id),
            CONSTRAINT fk_care_team_assignments_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_care_team_assignments_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_care_team_assignments_doctor_user_id
                FOREIGN KEY (doctor_user_id)
                REFERENCES clinic.staff_profiles (user_id) ON DELETE RESTRICT,
            CONSTRAINT fk_care_team_assignments_assigned_by FOREIGN KEY (assigned_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_care_team_assignments_ended_by FOREIGN KEY (ended_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_care_team_assignments_ended_at CHECK (
                ended_at >= assigned_at
            ),
            CONSTRAINT ck_care_team_assignments_end_reason_length CHECK (
                char_length(end_reason) BETWEEN 10 AND 500
            ),
            CONSTRAINT ck_care_team_assignments_end_reason CHECK (
                (ended_at IS NULL) = (end_reason IS NULL)
            )
        );
        CREATE UNIQUE INDEX ux_care_team_primary
            ON clinic.care_team_assignments (patient_id)
            WHERE is_primary AND ended_at IS NULL;
        CREATE UNIQUE INDEX ux_care_team_open_pair
            ON clinic.care_team_assignments (patient_id, doctor_user_id)
            WHERE ended_at IS NULL;
        CREATE INDEX ix_care_team_doctor_open
            ON clinic.care_team_assignments (doctor_user_id, patient_id)
            WHERE ended_at IS NULL;
        CREATE INDEX ix_care_team_patient_id
            ON clinic.care_team_assignments (patient_id, assigned_at DESC);
        COMMENT ON TABLE clinic.care_team_assignments IS
            'Doctor to patient assignments with history; source of the access proxy';

        CREATE FUNCTION clinic.ensure_primary_doctor() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, clinic
            AS $$
            BEGIN
                -- A patient with an open care team has exactly one open primary
                -- (the partial unique index already forbids two).
                IF EXISTS (
                    SELECT 1 FROM clinic.care_team_assignments AS a
                     WHERE a.patient_id = NEW.patient_id AND a.ended_at IS NULL
                ) AND NOT EXISTS (
                    SELECT 1 FROM clinic.care_team_assignments AS a
                     WHERE a.patient_id = NEW.patient_id
                       AND a.ended_at IS NULL AND a.is_primary
                ) THEN
                    RAISE EXCEPTION
                        'patient % has an open care team without a primary doctor',
                        NEW.patient_id
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NULL;
            END
            $$;
        COMMENT ON FUNCTION clinic.ensure_primary_doctor() IS
            'Deferred check: an open care team always has one primary doctor';
        CREATE CONSTRAINT TRIGGER tg_care_team_assignments_primary
            AFTER INSERT OR UPDATE ON clinic.care_team_assignments
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION clinic.ensure_primary_doctor();
        """
    )

    op.execute(
        """
        CREATE TABLE clinic.privacy_requests (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            request_type varchar(18) NOT NULL,
            status varchar(11) NOT NULL DEFAULT 'OPEN',
            description varchar(500),
            requested_at timestamptz NOT NULL DEFAULT now(),
            due_at timestamptz NOT NULL,
            resolved_at timestamptz,
            resolved_by uuid,
            resolution_note varchar(500),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_privacy_requests PRIMARY KEY (id),
            CONSTRAINT fk_privacy_requests_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_privacy_requests_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_privacy_requests_resolved_by FOREIGN KEY (resolved_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_privacy_requests_request_type CHECK (
                request_type IN (
                    'ACCESS', 'RECTIFICATION', 'ERASURE', 'CONSENT_WITHDRAWAL'
                )
            ),
            CONSTRAINT ck_privacy_requests_status CHECK (
                status IN (
                    'OPEN', 'IN_PROGRESS', 'APPROVED', 'REJECTED', 'COMPLETED'
                )
            ),
            CONSTRAINT ck_privacy_requests_due_at CHECK (due_at > requested_at),
            CONSTRAINT ck_privacy_requests_resolution_note_length CHECK (
                char_length(resolution_note) BETWEEN 10 AND 500
            )
        );
        CREATE INDEX ix_privacy_requests_open
            ON clinic.privacy_requests (clinic_id, due_at)
            WHERE status IN ('OPEN', 'IN_PROGRESS');
        CREATE INDEX ix_privacy_requests_patient_id
            ON clinic.privacy_requests (patient_id);
        COMMENT ON TABLE clinic.privacy_requests IS
            'Patient requests for access, rectification, erasure or withdrawal';
        COMMENT ON COLUMN clinic.privacy_requests.due_at IS
            'Response deadline; the legal term is pending (Cumplimiento)';

        CREATE TABLE clinic.import_rows (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            batch_id uuid NOT NULL,
            row_number integer NOT NULL,
            resource_type varchar(24),
            status varchar(17) NOT NULL,
            issue_codes varchar(40)[] NOT NULL DEFAULT '{}',
            mrn varchar(40),
            patient_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_import_rows PRIMARY KEY (id),
            CONSTRAINT fk_import_rows_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_import_rows_batch_id FOREIGN KEY (batch_id)
                REFERENCES clinic.import_batches (id) ON DELETE RESTRICT,
            CONSTRAINT fk_import_rows_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT ck_import_rows_row_number CHECK (row_number > 0),
            CONSTRAINT ck_import_rows_resource_type CHECK (
                resource_type IN (
                    'Patient', 'Practitioner', 'Condition', 'Observation',
                    'MedicationRequest'
                )
            ),
            CONSTRAINT ck_import_rows_status CHECK (
                status IN (
                    'CREATED', 'UPDATED', 'SKIPPED_DUPLICATE', 'REJECTED',
                    'WARNING', 'UNSUPPORTED'
                )
            )
        );
        CREATE UNIQUE INDEX ux_import_rows_batch_row
            ON clinic.import_rows (batch_id, row_number, resource_type)
            NULLS NOT DISTINCT;
        CREATE INDEX ix_import_rows_batch_status
            ON clinic.import_rows (batch_id, status);
        COMMENT ON TABLE clinic.import_rows IS
            'Per row result of an import batch; the raw row is never stored';
        COMMENT ON COLUMN clinic.import_rows.issue_codes IS
            'Issue codes without personal data (for example MISSING_BIRTH_DATE)';
        """
    )

    op.execute(
        """
        CREATE TABLE clinic.clinic_settings (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            recommendation_expiry_hours integer NOT NULL,
            forecast_refresh_minutes integer NOT NULL,
            dropout_inactivity_days integer NOT NULL,
            kpi_min_cell_size integer NOT NULL,
            updated_by uuid,
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_clinic_settings PRIMARY KEY (id),
            CONSTRAINT fk_clinic_settings_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinic_settings_updated_by FOREIGN KEY (updated_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinic_settings_recommendation_expiry_hours CHECK (
                recommendation_expiry_hours > 0
            ),
            CONSTRAINT ck_clinic_settings_forecast_refresh_minutes CHECK (
                forecast_refresh_minutes > 0
            ),
            CONSTRAINT ck_clinic_settings_dropout_inactivity_days CHECK (
                dropout_inactivity_days > 0
            ),
            CONSTRAINT ck_clinic_settings_kpi_min_cell_size CHECK (
                kpi_min_cell_size > 0
            ),
            CONSTRAINT ck_clinic_settings_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_clinic_settings_clinic
            ON clinic.clinic_settings (clinic_id) NULLS NOT DISTINCT;
        COMMENT ON TABLE clinic.clinic_settings IS
            'Operational parameters: a global row (clinic_id NULL) and one per clinic';
        """
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
            for table in RLS_TABLES
        )
    )

    # sites and staff_sites (RLS-1).
    op.execute(
        """
        CREATE POLICY sites_select ON clinic.sites FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
            );
        CREATE POLICY sites_insert_clinic_admin ON clinic.sites FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY sites_update_clinic_admin ON clinic.sites FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY staff_sites_select ON clinic.staff_sites FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
            );
        CREATE POLICY staff_sites_insert_clinic_admin ON clinic.staff_sites
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY staff_sites_delete_clinic_admin ON clinic.staff_sites
            FOR DELETE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        """
    )

    # care_team_assignments and privacy_requests (RLS-2).
    op.execute(
        """
        CREATE POLICY care_team_assignments_select
            ON clinic.care_team_assignments FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND (
                    iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
                    OR (
                        iam.app_role() = 'DOCTOR'
                        AND doctor_user_id = iam.app_user_id()
                    )
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND patient_id = clinic.current_patient_id()
                    )
                )
            );
        CREATE POLICY care_team_assignments_insert_clinic_admin
            ON clinic.care_team_assignments FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY care_team_assignments_update_clinic_admin
            ON clinic.care_team_assignments FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY privacy_requests_select ON clinic.privacy_requests
            FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND (
                    iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND patient_id = clinic.current_patient_id()
                    )
                )
            );
        CREATE POLICY privacy_requests_insert_own ON clinic.privacy_requests
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() = 'PATIENT'
                AND patient_id = clinic.current_patient_id()
            );
        CREATE POLICY privacy_requests_update_clinic_admin
            ON clinic.privacy_requests FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        """
    )

    # import_rows (RLS-1) and clinic_settings (RLS-3).
    op.execute(
        """
        CREATE POLICY import_rows_select_clinic_admin ON clinic.import_rows
            FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY import_rows_insert_clinic_admin ON clinic.import_rows
            FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );

        CREATE POLICY clinic_settings_select ON clinic.clinic_settings
            FOR SELECT
            USING (
                iam.app_clinic_id() IS NOT NULL
                AND iam.app_role() IN (
                    'CLINIC_ADMIN', 'DOCTOR', 'PATIENT', 'SYSTEM'
                )
                AND (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())
            );
        CREATE POLICY clinic_settings_insert_clinic_admin
            ON clinic.clinic_settings FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY clinic_settings_update_clinic_admin
            ON clinic.clinic_settings FOR UPDATE
            USING (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            )
            WITH CHECK (
                clinic_id = iam.app_clinic_id() AND iam.app_role() = 'CLINIC_ADMIN'
            );
        CREATE POLICY clinic_settings_migrator ON clinic.clinic_settings
            FOR ALL TO insumap_migrator USING (true) WITH CHECK (true);
        """
    )

    # insumap_app: minimum DML. staff_sites and the team lists are replaced by
    # the use cases, so staff_sites also needs DELETE (there is no active flag).
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON clinic.sites,
            clinic.care_team_assignments, clinic.privacy_requests,
            clinic.clinic_settings
            TO insumap_app;
        GRANT SELECT, INSERT, DELETE ON clinic.staff_sites TO insumap_app;
        GRANT SELECT, INSERT ON clinic.import_rows TO insumap_app;
        """
    )

    # insumap_readonly: SELECT without the free text a patient writes.
    op.execute(
        """
        GRANT SELECT ON clinic.sites, clinic.staff_sites,
            clinic.care_team_assignments, clinic.import_rows,
            clinic.clinic_settings
            TO insumap_readonly;
        GRANT SELECT (
            id, clinic_id, patient_id, request_type, status, requested_at,
            due_at, resolved_at, resolved_by, created_at, updated_at
        ) ON clinic.privacy_requests TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Revoke the privileges, drop the policies, the trigger and the tables."""
    tables = ", ".join(RLS_TABLES)
    op.execute(f"REVOKE ALL ON {tables} FROM insumap_app, insumap_readonly;")
    op.execute(
        "\n".join(
            f"DROP POLICY {policy} ON {table};" for policy, table in reversed(POLICIES)
        )
    )
    op.execute(
        """
        DROP TABLE clinic.clinic_settings;
        DROP TABLE clinic.import_rows;
        DROP TABLE clinic.privacy_requests;
        DROP TRIGGER tg_care_team_assignments_primary
            ON clinic.care_team_assignments;
        DROP FUNCTION clinic.ensure_primary_doctor();
        DROP TABLE clinic.care_team_assignments;
        DROP TABLE clinic.staff_sites;
        DROP TABLE clinic.sites;
        """
    )
