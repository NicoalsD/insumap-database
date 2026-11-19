"""Create the clinic tables needed by authentication and activation.

Modelo de datos 9.2, Diccionario section 5. This revision covers the tables
that GET /auth/me, login and activation read or write: clinics, import_batches
(target of patients.import_batch_id), staff_profiles, patients,
privacy_notice_versions and patient_consents. The rest of the clinic schema
(sites, staff_sites, care_team_assignments, privacy_requests, import_rows and
clinic_settings) comes with its own slice.

It also adds the foreign keys from iam to clinic that the iam revision could
not create yet.

Clinic lengths mirror the proposed FieldLimits constants of the Diccionario
(section 2). No clinical value appears in a DEFAULT or CHECK.

Revision ID: 6db90c757a50
Revises: 26c8fe803de9
Create Date: 2026-11-18 10:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "6db90c757a50"
down_revision: str | None = "26c8fe803de9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the clinic tables, their triggers and the iam to clinic FKs."""
    op.execute(
        """
        CREATE TABLE clinic.clinics (
            id uuid NOT NULL DEFAULT uuidv7(),
            name varchar(120) NOT NULL,
            tax_id varchar(32) NOT NULL,
            timezone varchar(64) NOT NULL,
            status varchar(9) NOT NULL DEFAULT 'ACTIVE',
            suspended_at timestamptz,
            suspended_reason varchar(500),
            is_demo boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_clinics PRIMARY KEY (id),
            CONSTRAINT ck_clinics_status CHECK (status IN ('ACTIVE', 'SUSPENDED')),
            CONSTRAINT ck_clinics_suspended CHECK (
                (status = 'SUSPENDED') = (suspended_at IS NOT NULL)
            ),
            CONSTRAINT ck_clinics_suspended_reason_length CHECK (
                char_length(suspended_reason) BETWEEN 10 AND 500
            ),
            CONSTRAINT ck_clinics_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_clinics_name_lower ON clinic.clinics (lower(name));
        CREATE INDEX ix_clinics_status ON clinic.clinics (status);
        COMMENT ON TABLE clinic.clinics IS
            'Contracting clinic (tenant); is_demo marks the simulated data clinic';
        COMMENT ON COLUMN clinic.clinics.timezone IS
            'IANA time zone, validated by the application';

        CREATE TABLE clinic.import_batches (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            source_format varchar(4) NOT NULL,
            data_origin varchar(9) NOT NULL DEFAULT 'REAL',
            status varchar(10) NOT NULL DEFAULT 'PREVIEWED',
            file_sha256 char(64) NOT NULL,
            file_size_bytes integer NOT NULL,
            simulator_version varchar(40),
            total_rows integer NOT NULL DEFAULT 0,
            valid_rows integer NOT NULL DEFAULT 0,
            warning_rows integer NOT NULL DEFAULT 0,
            error_rows integer NOT NULL DEFAULT 0,
            created_rows integer NOT NULL DEFAULT 0,
            skipped_rows integer NOT NULL DEFAULT 0,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            CONSTRAINT pk_import_batches PRIMARY KEY (id),
            CONSTRAINT fk_import_batches_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_import_batches_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_import_batches_source_format CHECK (
                source_format IN ('FHIR', 'CSV')
            ),
            CONSTRAINT ck_import_batches_data_origin CHECK (
                data_origin IN ('REAL', 'SIMULATED')
            ),
            CONSTRAINT ck_import_batches_status CHECK (
                status IN ('PREVIEWED', 'COMPLETED', 'REJECTED', 'FAILED')
            ),
            CONSTRAINT ck_import_batches_file_size_bytes CHECK (file_size_bytes > 0),
            CONSTRAINT ck_import_batches_counts CHECK (
                total_rows >= 0 AND valid_rows >= 0 AND warning_rows >= 0
                AND error_rows >= 0 AND created_rows >= 0 AND skipped_rows >= 0
            )
        );
        CREATE INDEX ix_import_batches_clinic_created
            ON clinic.import_batches (clinic_id, created_at DESC);
        CREATE INDEX ix_import_batches_file_sha256
            ON clinic.import_batches (clinic_id, file_sha256);
        COMMENT ON TABLE clinic.import_batches IS
            'Patient import batch (FHIR R4 or CSV); the file itself is not stored';
        """
    )

    op.execute(
        """
        CREATE TABLE clinic.staff_profiles (
            user_id uuid NOT NULL,
            clinic_id uuid NOT NULL,
            full_name varchar(120) NOT NULL,
            professional_license varchar(40),
            specialty varchar(80),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_staff_profiles PRIMARY KEY (user_id),
            CONSTRAINT fk_staff_profiles_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_staff_profiles_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_staff_profiles_clinic_license
            ON clinic.staff_profiles (clinic_id, professional_license)
            WHERE professional_license IS NOT NULL;
        CREATE INDEX ix_staff_profiles_clinic_id ON clinic.staff_profiles (clinic_id);
        COMMENT ON TABLE clinic.staff_profiles IS
            'Professional profile of doctors and administrators (1:1 with iam.users)';

        CREATE TABLE clinic.patients (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            user_id uuid NOT NULL,
            mrn varchar(40) NOT NULL,
            full_name varchar(120) NOT NULL,
            birth_date date NOT NULL,
            sex varchar(7) NOT NULL DEFAULT 'UNKNOWN',
            diabetes_type varchar(5) NOT NULL DEFAULT 'OTHER',
            diagnosis_date date,
            contact_email varchar(254),
            contact_phone varchar(32),
            document_type varchar(3) NOT NULL,
            document_hash char(64) NOT NULL,
            document_hash_key_id char(16) NOT NULL,
            document_ciphertext bytea NOT NULL,
            document_key_id char(16) NOT NULL,
            document_last4 varchar(4) NOT NULL,
            import_batch_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_patients PRIMARY KEY (id),
            CONSTRAINT fk_patients_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patients_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patients_import_batch_id FOREIGN KEY (import_batch_id)
                REFERENCES clinic.import_batches (id) ON DELETE RESTRICT,
            CONSTRAINT ck_patients_sex CHECK (
                sex IN ('FEMALE', 'MALE', 'OTHER', 'UNKNOWN')
            ),
            CONSTRAINT ck_patients_diabetes_type CHECK (
                diabetes_type IN ('T1D', 'T2D', 'GDM', 'OTHER')
            ),
            CONSTRAINT ck_patients_document_type CHECK (
                document_type IN ('CC', 'TI', 'RC', 'CE', 'PA', 'PEP', 'PPT')
            ),
            CONSTRAINT ck_patients_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_patients_clinic_mrn ON clinic.patients (clinic_id, mrn);
        CREATE UNIQUE INDEX ux_patients_clinic_document_hash
            ON clinic.patients (clinic_id, document_hash);
        CREATE UNIQUE INDEX ux_patients_user_id ON clinic.patients (user_id);
        CREATE INDEX ix_patients_clinic_name
            ON clinic.patients (clinic_id, lower(full_name));
        COMMENT ON TABLE clinic.patients IS
            'Patient identity (class C) linked to its iam.users account';
        COMMENT ON COLUMN clinic.patients.document_hash IS
            'HMAC-SHA256 of clinic, type and normalized number. Secret (class S)';
        COMMENT ON COLUMN clinic.patients.document_ciphertext IS
            'Document number encrypted with AES-256-GCM. Secret (class S)';
        """
    )

    op.execute(
        """
        CREATE TABLE clinic.privacy_notice_versions (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            version varchar(20) NOT NULL,
            content text NOT NULL,
            content_sha256 char(64) NOT NULL,
            effective_from timestamptz NOT NULL,
            is_current boolean NOT NULL DEFAULT false,
            published_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_privacy_notice_versions PRIMARY KEY (id),
            CONSTRAINT uq_privacy_notice_versions_clinic_version
                UNIQUE (clinic_id, version),
            CONSTRAINT fk_privacy_notice_versions_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_privacy_notice_versions_published_by
                FOREIGN KEY (published_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_privacy_notice_current
            ON clinic.privacy_notice_versions (clinic_id)
            WHERE is_current;
        COMMENT ON TABLE clinic.privacy_notice_versions IS
            'Versioned privacy notice of each clinic; the content is immutable';

        CREATE FUNCTION clinic.reject_notice_content_update() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'clinic.privacy_notice_versions rows are permanent';
                END IF;
                IF (NEW.id, NEW.clinic_id, NEW.version, NEW.content,
                    NEW.content_sha256, NEW.effective_from, NEW.published_by,
                    NEW.created_at)
                   IS DISTINCT FROM
                   (OLD.id, OLD.clinic_id, OLD.version, OLD.content,
                    OLD.content_sha256, OLD.effective_from, OLD.published_by,
                    OLD.created_at)
                THEN
                    RAISE EXCEPTION
                        'clinic.privacy_notice_versions: only is_current may change';
                END IF;
                RETURN NEW;
            END
            $$;
        CREATE TRIGGER tg_privacy_notice_versions_immutable
            BEFORE UPDATE OR DELETE ON clinic.privacy_notice_versions
            FOR EACH ROW EXECUTE FUNCTION clinic.reject_notice_content_update();

        CREATE TABLE clinic.patient_consents (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            privacy_notice_version_id uuid NOT NULL,
            scope varchar(26) NOT NULL,
            action varchar(9) NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            ip_hmac char(64),
            recorded_by uuid NOT NULL,
            CONSTRAINT pk_patient_consents PRIMARY KEY (id),
            CONSTRAINT fk_patient_consents_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_consents_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_consents_privacy_notice_version_id
                FOREIGN KEY (privacy_notice_version_id)
                REFERENCES clinic.privacy_notice_versions (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_consents_recorded_by FOREIGN KEY (recorded_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_patient_consents_scope CHECK (
                scope IN ('SENSITIVE_DATA_PROCESSING', 'AI_EXTERNAL_PROCESSING')
            ),
            CONSTRAINT ck_patient_consents_action CHECK (
                action IN ('ACCEPTED', 'WITHDRAWN')
            )
        );
        CREATE INDEX ix_patient_consents_latest
            ON clinic.patient_consents (patient_id, scope, occurred_at DESC);
        CREATE TRIGGER tg_patient_consents_append_only
            BEFORE UPDATE OR DELETE ON clinic.patient_consents
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
        COMMENT ON TABLE clinic.patient_consents IS
            'Append-only consent history per notice version and scope';
        """
    )

    # Foreign keys from iam that needed the clinic tables.
    op.execute(
        """
        ALTER TABLE iam.memberships
            ADD CONSTRAINT fk_memberships_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT;
        ALTER TABLE iam.credential_invitations
            ADD CONSTRAINT fk_credential_invitations_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_credential_invitations_patient_id
                FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT;
        ALTER TABLE iam.auth_challenges
            ADD CONSTRAINT fk_auth_challenges_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT;
        ALTER TABLE iam.refresh_tokens
            ADD CONSTRAINT fk_refresh_tokens_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT;
        ALTER TABLE iam.security_events
            ADD CONSTRAINT fk_security_events_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT;
        """
    )


def downgrade() -> None:
    """Drop the iam to clinic FKs and the clinic tables."""
    op.execute(
        """
        ALTER TABLE iam.security_events DROP CONSTRAINT fk_security_events_clinic_id;
        ALTER TABLE iam.refresh_tokens DROP CONSTRAINT fk_refresh_tokens_clinic_id;
        ALTER TABLE iam.auth_challenges DROP CONSTRAINT fk_auth_challenges_clinic_id;
        ALTER TABLE iam.credential_invitations
            DROP CONSTRAINT fk_credential_invitations_patient_id,
            DROP CONSTRAINT fk_credential_invitations_clinic_id;
        ALTER TABLE iam.memberships DROP CONSTRAINT fk_memberships_clinic_id;

        DROP TABLE clinic.patient_consents;
        DROP TABLE clinic.privacy_notice_versions;
        DROP FUNCTION clinic.reject_notice_content_update();
        DROP TABLE clinic.patients;
        DROP TABLE clinic.staff_profiles;
        DROP TABLE clinic.import_batches;
        DROP TABLE clinic.clinics;
        """
    )
