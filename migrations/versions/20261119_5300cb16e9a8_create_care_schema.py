"""Create the care schema: plans, goals, reminders, follow-ups and notes.

Modelo de datos 9.3, Diccionario section 6. Thirteen tables:
target_profiles, patient_target_assignments, protocol_templates,
treatment_plans, treatment_plan_items, treatment_plan_snapshots,
reminder_schedules, follow_ups, clinical_notes, triage_reviews,
reading_validation_settings, symptom_catalog and glycemic_metric_snapshots.

No clinical value appears in a DEFAULT or CHECK (AGENTS 3.2): the CHECKs only
state technical coherence (positive values, ordered limits, percentages between
0 and 100). The clinical parameters are rows (see the seed revisions).

Foreign keys to the ai schema (treatment_plans.source_recommendation_id,
follow_ups.risk_assessment_id and triage_reviews.risk_assessment_id) are added
by the ai revision, which creates the referenced tables.

Triggers:

- care.reject_active_version_update(): an ACTIVE version of target_profiles,
  protocol_templates and reading_validation_settings only admits the change to
  RETIRED. alerts.alert_rules reuses it in its own revision.
- care.guard_plan_items_draft(): the items of a plan change only while the
  plan is DRAFT, and an ACTIVE plan only moves to SUPERSEDED.
- audit.reject_mutation(): treatment_plan_snapshots, clinical_notes,
  triage_reviews and glycemic_metric_snapshots are append-only.

Row level security (Modelo de datos 4.3 to 4.5). CLINIC_ADMIN manages the
catalogs (target_profiles, protocol_templates, reading_validation_settings and
symptom_catalog, RLS-3 with a migrator policy for the seeds) but has no access
to the clinical tables. DOCTOR and SYSTEM work on the clinic of the session; the
assignment of a doctor to a patient is checked by PatientRecordAccessProxy, not
by these policies (Modelo de datos 4.4, point 2). A PATIENT reads only its own
rows (plan, reminders, metrics and goals).

Revision ID: 5300cb16e9a8
Revises: 7e0f37fe0a9f
Create Date: 2026-11-19 09:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5300cb16e9a8"
down_revision: str | None = "7e0f37fe0a9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "care.target_profiles",
    "care.patient_target_assignments",
    "care.protocol_templates",
    "care.treatment_plans",
    "care.treatment_plan_items",
    "care.treatment_plan_snapshots",
    "care.reminder_schedules",
    "care.follow_ups",
    "care.clinical_notes",
    "care.triage_reviews",
    "care.reading_validation_settings",
    "care.symptom_catalog",
    "care.glycemic_metric_snapshots",
)

APPEND_ONLY = (
    "treatment_plan_snapshots",
    "clinical_notes",
    "triage_reviews",
    "glycemic_metric_snapshots",
)

# Fragments shared by the policies.
IN_CLINIC = "clinic_id = iam.app_clinic_id()"
OWN_PATIENT = "iam.app_role() = 'PATIENT' AND patient_id = clinic.current_patient_id()"
CLINICAL_STAFF = "iam.app_role() IN ('DOCTOR', 'SYSTEM')"
# Global rows (clinic_id NULL) and the session clinic, for the roles of a clinic.
SEED_READ = (
    "iam.app_clinic_id() IS NOT NULL"
    " AND iam.app_role() IN {roles}"
    " AND (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())"
)
ADMIN_ONLY = f"{IN_CLINIC} AND iam.app_role() = 'CLINIC_ADMIN'"

# (policy, table) pairs created by this revision, dropped in reverse order.
POLICIES = (
    ("target_profiles_select", "care.target_profiles"),
    ("target_profiles_insert_clinic_admin", "care.target_profiles"),
    ("target_profiles_update_clinic_admin", "care.target_profiles"),
    ("target_profiles_migrator", "care.target_profiles"),
    ("patient_target_assignments_select", "care.patient_target_assignments"),
    ("patient_target_assignments_insert_doctor", "care.patient_target_assignments"),
    ("patient_target_assignments_update_doctor", "care.patient_target_assignments"),
    ("protocol_templates_select", "care.protocol_templates"),
    ("protocol_templates_insert_clinic_admin", "care.protocol_templates"),
    ("protocol_templates_update_clinic_admin", "care.protocol_templates"),
    ("protocol_templates_migrator", "care.protocol_templates"),
    ("treatment_plans_select", "care.treatment_plans"),
    ("treatment_plans_insert_staff", "care.treatment_plans"),
    ("treatment_plans_update_staff", "care.treatment_plans"),
    ("treatment_plan_items_select", "care.treatment_plan_items"),
    ("treatment_plan_items_insert_staff", "care.treatment_plan_items"),
    ("treatment_plan_items_update_staff", "care.treatment_plan_items"),
    ("treatment_plan_items_delete_staff", "care.treatment_plan_items"),
    ("treatment_plan_snapshots_select", "care.treatment_plan_snapshots"),
    ("treatment_plan_snapshots_insert", "care.treatment_plan_snapshots"),
    ("reminder_schedules_select", "care.reminder_schedules"),
    ("reminder_schedules_insert_doctor", "care.reminder_schedules"),
    ("reminder_schedules_update", "care.reminder_schedules"),
    ("follow_ups_select", "care.follow_ups"),
    ("follow_ups_insert", "care.follow_ups"),
    ("follow_ups_update", "care.follow_ups"),
    ("clinical_notes_select", "care.clinical_notes"),
    ("clinical_notes_insert", "care.clinical_notes"),
    ("triage_reviews_select", "care.triage_reviews"),
    ("triage_reviews_insert", "care.triage_reviews"),
    ("reading_validation_settings_select", "care.reading_validation_settings"),
    (
        "reading_validation_settings_insert_clinic_admin",
        "care.reading_validation_settings",
    ),
    (
        "reading_validation_settings_update_clinic_admin",
        "care.reading_validation_settings",
    ),
    ("reading_validation_settings_migrator", "care.reading_validation_settings"),
    ("symptom_catalog_select", "care.symptom_catalog"),
    ("symptom_catalog_insert_clinic_admin", "care.symptom_catalog"),
    ("symptom_catalog_update_clinic_admin", "care.symptom_catalog"),
    ("symptom_catalog_migrator", "care.symptom_catalog"),
    ("glycemic_metric_snapshots_select", "care.glycemic_metric_snapshots"),
    ("glycemic_metric_snapshots_insert", "care.glycemic_metric_snapshots"),
)


def upgrade() -> None:
    """Create the schema, tables, triggers, policies and grants."""
    op.execute(
        """
        CREATE SCHEMA care;
        COMMENT ON SCHEMA care IS 'Treatment plans, goals and follow-up';
        GRANT USAGE ON SCHEMA care
            TO insumap_app, insumap_readonly, insumap_definer;
        """
    )

    op.execute(
        """
        CREATE TABLE care.target_profiles (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            code varchar(40) NOT NULL,
            label varchar(120) NOT NULL,
            population varchar(20) NOT NULL,
            version integer NOT NULL,
            status varchar(7) NOT NULL DEFAULT 'DRAFT',
            is_default boolean NOT NULL DEFAULT false,
            range_low_mg_dl numeric(5,1) NOT NULL,
            range_high_mg_dl numeric(5,1) NOT NULL,
            very_low_mg_dl numeric(5,1) NOT NULL,
            very_high_mg_dl numeric(5,1) NOT NULL,
            tir_min_pct numeric(5,2) NOT NULL,
            tbr_l1_max_pct numeric(5,2) NOT NULL,
            tbr_l2_max_pct numeric(5,2) NOT NULL,
            tar_l1_max_pct numeric(5,2) NOT NULL,
            tar_l2_max_pct numeric(5,2) NOT NULL,
            cv_max_pct numeric(5,2) NOT NULL,
            min_data_sufficiency_pct numeric(5,2) NOT NULL,
            min_days smallint NOT NULL,
            max_gap_minutes smallint NOT NULL,
            hypo_episode_min_minutes smallint NOT NULL,
            min_readings_for_cv integer NOT NULL,
            min_readings_for_cv_smbg integer NOT NULL,
            hba1c_goal_pct numeric(4,1),
            fasting_low_mg_dl numeric(5,1),
            fasting_high_mg_dl numeric(5,1),
            postprandial_max_mg_dl numeric(5,1),
            postprandial_1h_max_mg_dl numeric(5,1),
            postprandial_2h_max_mg_dl numeric(5,1),
            night_start time NOT NULL,
            night_end time NOT NULL,
            source_reference varchar(300) NOT NULL,
            change_reason varchar(500),
            created_by uuid,
            activated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_target_profiles PRIMARY KEY (id),
            CONSTRAINT fk_target_profiles_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_target_profiles_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_target_profiles_code CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_target_profiles_population CHECK (
                population IN (
                    'ADULT', 'OLDER_OR_HIGH_RISK', 'PREGNANCY_T1D',
                    'PREGNANCY_T2D_GDM'
                )
            ),
            CONSTRAINT ck_target_profiles_version CHECK (version > 0),
            CONSTRAINT ck_target_profiles_status CHECK (
                status IN ('DRAFT', 'ACTIVE', 'RETIRED')
            ),
            CONSTRAINT ck_target_profiles_range CHECK (
                range_low_mg_dl > 0 AND range_high_mg_dl > range_low_mg_dl
            ),
            CONSTRAINT ck_target_profiles_very_low CHECK (
                very_low_mg_dl > 0 AND very_low_mg_dl < range_low_mg_dl
            ),
            CONSTRAINT ck_target_profiles_very_high CHECK (
                very_high_mg_dl > range_high_mg_dl
            ),
            CONSTRAINT ck_target_profiles_tir_min_pct CHECK (
                tir_min_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_tbr_l1_max_pct CHECK (
                tbr_l1_max_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_tbr_l2_max_pct CHECK (
                tbr_l2_max_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_tar_l1_max_pct CHECK (
                tar_l1_max_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_tar_l2_max_pct CHECK (
                tar_l2_max_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_cv_max_pct CHECK (
                cv_max_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_min_data_sufficiency_pct CHECK (
                min_data_sufficiency_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_target_profiles_min_days CHECK (min_days > 0),
            CONSTRAINT ck_target_profiles_max_gap_minutes CHECK (
                max_gap_minutes > 0
            ),
            CONSTRAINT ck_target_profiles_hypo_episode_min_minutes CHECK (
                hypo_episode_min_minutes > 0
            ),
            CONSTRAINT ck_target_profiles_min_readings_for_cv CHECK (
                min_readings_for_cv > 0
            ),
            CONSTRAINT ck_target_profiles_min_readings_for_cv_smbg CHECK (
                min_readings_for_cv_smbg > 0
            ),
            CONSTRAINT ck_target_profiles_hba1c_goal_pct CHECK (hba1c_goal_pct > 0),
            CONSTRAINT ck_target_profiles_fasting_low CHECK (fasting_low_mg_dl > 0),
            CONSTRAINT ck_target_profiles_fasting_high CHECK (
                fasting_high_mg_dl >= fasting_low_mg_dl
            ),
            CONSTRAINT ck_target_profiles_postprandial_max CHECK (
                postprandial_max_mg_dl > 0
            ),
            CONSTRAINT ck_target_profiles_postprandial_1h_max CHECK (
                postprandial_1h_max_mg_dl > 0
            ),
            CONSTRAINT ck_target_profiles_postprandial_2h_max CHECK (
                postprandial_2h_max_mg_dl > 0
            ),
            CONSTRAINT ck_target_profiles_change_reason_length CHECK (
                char_length(change_reason) BETWEEN 10 AND 500
            )
        );
        CREATE UNIQUE INDEX ux_target_profiles_version
            ON care.target_profiles (clinic_id, code, version) NULLS NOT DISTINCT;
        CREATE UNIQUE INDEX ux_target_profiles_active
            ON care.target_profiles (clinic_id, code) NULLS NOT DISTINCT
            WHERE status = 'ACTIVE';
        CREATE INDEX ix_target_profiles_population_default
            ON care.target_profiles (population)
            WHERE is_default AND status = 'ACTIVE';
        COMMENT ON TABLE care.target_profiles IS
            'Versioned glycemic goals; seeds have clinic_id NULL and cite their source';
        COMMENT ON COLUMN care.target_profiles.tbr_l1_max_pct IS
            'Maximum time below range_low_mg_dl (total, levels 1 and 2)';
        COMMENT ON COLUMN care.target_profiles.tar_l1_max_pct IS
            'Maximum time above range_high_mg_dl (total, levels 1 and 2)';

        CREATE TABLE care.patient_target_assignments (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            target_profile_id uuid NOT NULL,
            assigned_by uuid NOT NULL,
            assigned_at timestamptz NOT NULL DEFAULT now(),
            ended_at timestamptz,
            reason varchar(500),
            CONSTRAINT pk_patient_target_assignments PRIMARY KEY (id),
            CONSTRAINT fk_patient_target_assignments_clinic_id
                FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_target_assignments_patient_id
                FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_target_assignments_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT fk_patient_target_assignments_assigned_by
                FOREIGN KEY (assigned_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_patient_target_assignments_ended_at CHECK (
                ended_at >= assigned_at
            ),
            CONSTRAINT ck_patient_target_assignments_reason_length CHECK (
                char_length(reason) BETWEEN 10 AND 500
            )
        );
        CREATE UNIQUE INDEX ux_patient_target_open
            ON care.patient_target_assignments (patient_id)
            WHERE ended_at IS NULL;
        COMMENT ON TABLE care.patient_target_assignments IS
            'Goal profile assigned to each patient; without one the default applies';

        CREATE TABLE care.protocol_templates (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            code varchar(40) NOT NULL,
            name varchar(120) NOT NULL,
            description varchar(500),
            diabetes_type varchar(5),
            version integer NOT NULL,
            status varchar(7) NOT NULL DEFAULT 'DRAFT',
            default_target_profile_id uuid,
            content jsonb NOT NULL,
            created_by uuid,
            activated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_protocol_templates PRIMARY KEY (id),
            CONSTRAINT fk_protocol_templates_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_protocol_templates_default_target_profile_id
                FOREIGN KEY (default_target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT fk_protocol_templates_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_protocol_templates_code CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_protocol_templates_diabetes_type CHECK (
                diabetes_type IN ('T1D', 'T2D', 'GDM', 'OTHER')
            ),
            CONSTRAINT ck_protocol_templates_version CHECK (version > 0),
            CONSTRAINT ck_protocol_templates_status CHECK (
                status IN ('DRAFT', 'ACTIVE', 'RETIRED')
            ),
            CONSTRAINT ck_protocol_templates_content CHECK (
                jsonb_typeof(content) = 'object'
            )
        );
        CREATE UNIQUE INDEX ux_protocol_templates_version
            ON care.protocol_templates (clinic_id, code, version)
            NULLS NOT DISTINCT;
        CREATE INDEX ix_protocol_templates_active
            ON care.protocol_templates (clinic_id, code)
            WHERE status = 'ACTIVE';
        COMMENT ON TABLE care.protocol_templates IS
            'Versioned protocol templates (prototype P05 of the plan content)';
        """
    )

    op.execute(
        """
        CREATE TABLE care.treatment_plans (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            version integer NOT NULL,
            status varchar(10) NOT NULL DEFAULT 'DRAFT',
            template_id uuid,
            previous_plan_id uuid,
            source_recommendation_id uuid,
            target_profile_id uuid NOT NULL,
            titration_rules jsonb,
            notes varchar(1000),
            created_by uuid NOT NULL,
            activated_by uuid,
            activated_at timestamptz,
            superseded_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_treatment_plans PRIMARY KEY (id),
            CONSTRAINT fk_treatment_plans_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_template_id FOREIGN KEY (template_id)
                REFERENCES care.protocol_templates (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_previous_plan_id
                FOREIGN KEY (previous_plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plans_activated_by FOREIGN KEY (activated_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_treatment_plans_version CHECK (version > 0),
            CONSTRAINT ck_treatment_plans_status CHECK (
                status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'DISCARDED')
            ),
            CONSTRAINT ck_treatment_plans_titration_rules CHECK (
                jsonb_typeof(titration_rules) = 'object'
            ),
            CONSTRAINT ck_treatment_plans_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_treatment_plans_version
            ON care.treatment_plans (patient_id, version);
        CREATE UNIQUE INDEX ux_treatment_plans_active
            ON care.treatment_plans (patient_id) WHERE status = 'ACTIVE';
        CREATE INDEX ix_treatment_plans_patient_status
            ON care.treatment_plans (patient_id, status);
        COMMENT ON TABLE care.treatment_plans IS
            'Versioned treatment plan; one ACTIVE plan per patient';

        CREATE TABLE care.treatment_plan_items (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            plan_id uuid NOT NULL,
            item_type varchar(10) NOT NULL,
            insulin_kind varchar(10),
            name varchar(120) NOT NULL,
            dose_units numeric(6,2),
            time_of_day time,
            days_of_week smallint[],
            instructions varchar(500),
            sort_order smallint NOT NULL DEFAULT 0,
            CONSTRAINT pk_treatment_plan_items PRIMARY KEY (id),
            CONSTRAINT fk_treatment_plan_items_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plan_items_plan_id FOREIGN KEY (plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT ck_treatment_plan_items_item_type CHECK (
                item_type IN ('INSULIN', 'MEDICATION', 'MEASURE')
            ),
            CONSTRAINT ck_treatment_plan_items_insulin_kind CHECK (
                insulin_kind IN ('BASAL', 'BOLUS', 'CORRECTION', 'MIXED')
            ),
            CONSTRAINT ck_treatment_plan_items_insulin CHECK (
                (item_type = 'INSULIN') = (insulin_kind IS NOT NULL)
            ),
            CONSTRAINT ck_treatment_plan_items_dose_units CHECK (dose_units > 0),
            CONSTRAINT ck_treatment_plan_items_days_of_week CHECK (
                days_of_week <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]
            ),
            CONSTRAINT ck_treatment_plan_items_sort_order CHECK (sort_order >= 0)
        );
        CREATE INDEX ix_treatment_plan_items_plan_id
            ON care.treatment_plan_items (plan_id, sort_order);
        COMMENT ON TABLE care.treatment_plan_items IS
            'Insulins, medications and measurements of a plan; editable in DRAFT only';

        CREATE TABLE care.treatment_plan_snapshots (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            plan_id uuid NOT NULL,
            snapshot_no integer NOT NULL,
            snapshot jsonb NOT NULL,
            taken_by uuid NOT NULL,
            taken_at timestamptz NOT NULL DEFAULT now(),
            reason varchar(500),
            CONSTRAINT pk_treatment_plan_snapshots PRIMARY KEY (id),
            CONSTRAINT fk_treatment_plan_snapshots_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plan_snapshots_plan_id FOREIGN KEY (plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT fk_treatment_plan_snapshots_taken_by FOREIGN KEY (taken_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT uq_treatment_plan_snapshots_plan_no
                UNIQUE (plan_id, snapshot_no),
            CONSTRAINT ck_treatment_plan_snapshots_snapshot_no CHECK (
                snapshot_no > 0
            ),
            CONSTRAINT ck_treatment_plan_snapshots_snapshot CHECK (
                jsonb_typeof(snapshot) = 'object'
            ),
            CONSTRAINT ck_treatment_plan_snapshots_reason_length CHECK (
                char_length(reason) BETWEEN 10 AND 500
            )
        );
        COMMENT ON TABLE care.treatment_plan_snapshots IS
            'Plan memento (P16): jsonb photo before each change; append-only';
        """
    )

    op.execute(
        """
        CREATE TABLE care.reminder_schedules (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            plan_id uuid,
            reminder_type varchar(12) NOT NULL,
            local_time time NOT NULL,
            days_of_week smallint[],
            message_key varchar(60) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            set_by uuid NOT NULL,
            last_sent_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reminder_schedules PRIMARY KEY (id),
            CONSTRAINT fk_reminder_schedules_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reminder_schedules_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reminder_schedules_plan_id FOREIGN KEY (plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reminder_schedules_set_by FOREIGN KEY (set_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_reminder_schedules_reminder_type CHECK (
                reminder_type IN (
                    'MEASUREMENT', 'INSULIN_DOSE', 'CONTROL', 'REGISTRATION'
                )
            ),
            CONSTRAINT ck_reminder_schedules_days_of_week CHECK (
                days_of_week <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]
            )
        );
        CREATE INDEX ix_reminder_schedules_due
            ON care.reminder_schedules (local_time) WHERE is_active;
        CREATE INDEX ix_reminder_schedules_patient_id
            ON care.reminder_schedules (patient_id);
        COMMENT ON TABLE care.reminder_schedules IS
            'Scheduled reminders in clinic local time; evaluated every minute';

        CREATE TABLE care.follow_ups (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            kind varchar(8) NOT NULL,
            status varchar(9) NOT NULL DEFAULT 'SCHEDULED',
            scheduled_for timestamptz,
            assigned_to uuid,
            source varchar(10) NOT NULL DEFAULT 'MANUAL',
            risk_assessment_id uuid,
            title varchar(120) NOT NULL,
            result_note varchar(500),
            created_by uuid NOT NULL,
            closed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_follow_ups PRIMARY KEY (id),
            CONSTRAINT fk_follow_ups_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_follow_ups_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_follow_ups_assigned_to FOREIGN KEY (assigned_to)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_follow_ups_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_follow_ups_kind CHECK (kind IN ('CONTROL', 'OUTREACH')),
            CONSTRAINT ck_follow_ups_status CHECK (
                status IN (
                    'SCHEDULED', 'OPEN', 'DONE', 'MISSED', 'CANCELLED', 'DISMISSED'
                )
            ),
            CONSTRAINT ck_follow_ups_kind_status CHECK (
                (kind = 'CONTROL'
                 AND status IN ('SCHEDULED', 'DONE', 'MISSED', 'CANCELLED'))
                OR (kind = 'OUTREACH' AND status IN ('OPEN', 'DONE', 'DISMISSED'))
            ),
            CONSTRAINT ck_follow_ups_source CHECK (
                source IN ('MANUAL', 'AI_DROPOUT', 'RULE')
            ),
            CONSTRAINT ck_follow_ups_result_note_length CHECK (
                char_length(result_note) BETWEEN 10 AND 500
            )
        );
        CREATE INDEX ix_follow_ups_patient_status
            ON care.follow_ups (patient_id, status);
        CREATE INDEX ix_follow_ups_due
            ON care.follow_ups (scheduled_for) WHERE status = 'SCHEDULED';
        COMMENT ON TABLE care.follow_ups IS
            'Follow-up controls and outreach tasks for patients at risk of dropout';

        CREATE TABLE care.clinical_notes (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            author_user_id uuid NOT NULL,
            body varchar(2000) NOT NULL,
            supersedes_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_clinical_notes PRIMARY KEY (id),
            CONSTRAINT fk_clinical_notes_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinical_notes_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinical_notes_author_user_id FOREIGN KEY (author_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinical_notes_supersedes_id FOREIGN KEY (supersedes_id)
                REFERENCES care.clinical_notes (id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinical_notes_body CHECK (char_length(body) > 0)
        );
        CREATE INDEX ix_clinical_notes_patient_created
            ON care.clinical_notes (patient_id, created_at DESC);
        COMMENT ON TABLE care.clinical_notes IS
            'Append-only doctor notes; a wrong note is fixed by a superseding note';

        CREATE TABLE care.triage_reviews (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            reviewed_by uuid NOT NULL,
            risk_assessment_id uuid,
            window_days smallint NOT NULL,
            reviewed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_triage_reviews PRIMARY KEY (id),
            CONSTRAINT fk_triage_reviews_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_triage_reviews_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_triage_reviews_reviewed_by FOREIGN KEY (reviewed_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_triage_reviews_window_days CHECK (window_days > 0)
        );
        CREATE INDEX ix_triage_reviews_latest
            ON care.triage_reviews (patient_id, reviewed_by, reviewed_at DESC);
        COMMENT ON TABLE care.triage_reviews IS
            'Reviewed marks of the triage dashboard (RF-79); the latest row wins';
        """
    )

    op.execute(
        """
        CREATE TABLE care.reading_validation_settings (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            version integer NOT NULL,
            status varchar(7) NOT NULL DEFAULT 'DRAFT',
            min_measurable_mg_dl numeric(5,1) NOT NULL,
            max_measurable_mg_dl numeric(5,1) NOT NULL,
            future_tolerance_seconds integer NOT NULL,
            max_reading_age_hours integer NOT NULL,
            duplicate_window_seconds integer NOT NULL,
            max_rate_mg_dl_per_min numeric(5,2) NOT NULL,
            unit_mismatch_ratio numeric(5,2) NOT NULL,
            created_by uuid,
            activated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reading_validation_settings PRIMARY KEY (id),
            CONSTRAINT fk_reading_validation_settings_clinic_id
                FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reading_validation_settings_created_by
                FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_reading_validation_settings_version CHECK (version > 0),
            CONSTRAINT ck_reading_validation_settings_status CHECK (
                status IN ('DRAFT', 'ACTIVE', 'RETIRED')
            ),
            CONSTRAINT ck_reading_validation_settings_min_measurable CHECK (
                min_measurable_mg_dl > 0
            ),
            CONSTRAINT ck_reading_validation_settings_max_measurable CHECK (
                max_measurable_mg_dl > min_measurable_mg_dl
            ),
            CONSTRAINT ck_reading_validation_settings_future_tolerance CHECK (
                future_tolerance_seconds >= 0
            ),
            CONSTRAINT ck_reading_validation_settings_max_reading_age CHECK (
                max_reading_age_hours > 0
            ),
            CONSTRAINT ck_reading_validation_settings_duplicate_window CHECK (
                duplicate_window_seconds > 0
            ),
            CONSTRAINT ck_reading_validation_settings_max_rate CHECK (
                max_rate_mg_dl_per_min > 0
            ),
            CONSTRAINT ck_reading_validation_settings_unit_mismatch_ratio CHECK (
                unit_mismatch_ratio > 1
            )
        );
        CREATE UNIQUE INDEX ux_reading_validation_version
            ON care.reading_validation_settings (clinic_id, version)
            NULLS NOT DISTINCT;
        CREATE UNIQUE INDEX ux_reading_validation_active
            ON care.reading_validation_settings (clinic_id) NULLS NOT DISTINCT
            WHERE status = 'ACTIVE';
        COMMENT ON TABLE care.reading_validation_settings IS
            'Versioned glucose validation parameters; no threshold lives in code';

        CREATE TABLE care.symptom_catalog (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            code varchar(32) NOT NULL,
            label_es varchar(80) NOT NULL,
            is_alarm boolean NOT NULL DEFAULT false,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_symptom_catalog PRIMARY KEY (id),
            CONSTRAINT fk_symptom_catalog_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT ck_symptom_catalog_code CHECK (code ~ '^[A-Z][A-Z0-9_]*$')
        );
        CREATE UNIQUE INDEX ux_symptom_catalog_code
            ON care.symptom_catalog (clinic_id, code) NULLS NOT DISTINCT;
        COMMENT ON TABLE care.symptom_catalog IS
            'Symptoms a patient can record; is_alarm raises the emergency notice';

        CREATE TABLE care.glycemic_metric_snapshots (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            target_profile_id uuid NOT NULL,
            window_days smallint NOT NULL,
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now(),
            signal varchar(4) NOT NULL,
            data_sufficiency_pct numeric(5,1) NOT NULL,
            confidence varchar(14) NOT NULL,
            mean_glucose_mg_dl numeric(5,1),
            tir_pct numeric(5,2),
            tbr_l1_pct numeric(5,2),
            tbr_l2_pct numeric(5,2),
            tar_l1_pct numeric(5,2),
            tar_l2_pct numeric(5,2),
            gmi_pct numeric(5,2),
            cv_pct numeric(5,2),
            hypo_events_count smallint,
            hypo_events_night_count smallint,
            tir_delta_vs_prior_pp numeric(5,2),
            agp jsonb,
            CONSTRAINT pk_glycemic_metric_snapshots PRIMARY KEY (id),
            CONSTRAINT fk_glycemic_metric_snapshots_clinic_id
                FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_glycemic_metric_snapshots_patient_id
                FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_glycemic_metric_snapshots_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT ck_glycemic_metric_snapshots_window_days CHECK (
                window_days > 0
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_window CHECK (
                window_end > window_start
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_signal CHECK (
                signal IN ('CGM', 'SMBG')
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_data_sufficiency_pct CHECK (
                data_sufficiency_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_confidence CHECK (
                confidence IN ('OK', 'LOW_CONFIDENCE')
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_tir_pct CHECK (
                tir_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_tbr_l1_pct CHECK (
                tbr_l1_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_tbr_l2_pct CHECK (
                tbr_l2_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_tar_l1_pct CHECK (
                tar_l1_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_tar_l2_pct CHECK (
                tar_l2_pct BETWEEN 0 AND 100
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_hypo_events_count CHECK (
                hypo_events_count >= 0
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_hypo_events_night_count CHECK (
                hypo_events_night_count >= 0
            ),
            CONSTRAINT ck_glycemic_metric_snapshots_agp CHECK (
                jsonb_typeof(agp) = 'object'
            )
        );
        CREATE INDEX ix_glycemic_metrics_latest
            ON care.glycemic_metric_snapshots
            (patient_id, window_days, computed_at DESC);
        COMMENT ON TABLE care.glycemic_metric_snapshots IS
            'Computed metrics per window with the goal profile version used';
        COMMENT ON COLUMN care.glycemic_metric_snapshots.tir_delta_vs_prior_pp IS
            'TIR change against the prior period in percentage points';
        """
    )

    # Triggers.
    op.execute(
        """
        CREATE FUNCTION care.reject_active_version_update() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $$
            BEGIN
                -- An ACTIVE version is immutable: it only moves to RETIRED.
                IF OLD.status = 'ACTIVE'
                   AND NOT (
                       NEW.status = 'RETIRED'
                       AND to_jsonb(NEW) - 'status' = to_jsonb(OLD) - 'status'
                   )
                THEN
                    RAISE EXCEPTION
                        '%.% is ACTIVE: only the change to RETIRED is allowed',
                        TG_TABLE_SCHEMA, TG_TABLE_NAME
                        USING HINT = 'Create a new version instead of editing it.';
                END IF;
                RETURN NEW;
            END
            $$;
        COMMENT ON FUNCTION care.reject_active_version_update() IS
            'BEFORE UPDATE trigger of the versioned configuration tables';

        CREATE TRIGGER tg_target_profiles_active_version
            BEFORE UPDATE ON care.target_profiles
            FOR EACH ROW EXECUTE FUNCTION care.reject_active_version_update();
        CREATE TRIGGER tg_protocol_templates_active_version
            BEFORE UPDATE ON care.protocol_templates
            FOR EACH ROW EXECUTE FUNCTION care.reject_active_version_update();
        CREATE TRIGGER tg_reading_validation_settings_active_version
            BEFORE UPDATE ON care.reading_validation_settings
            FOR EACH ROW EXECUTE FUNCTION care.reject_active_version_update();

        CREATE FUNCTION care.guard_plan_items_draft() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, care
            AS $$
            DECLARE
                plan_status varchar;
                plan_ids uuid[];
                plan_id uuid;
            BEGIN
                IF TG_TABLE_NAME = 'treatment_plans' THEN
                    -- An ACTIVE plan only moves to SUPERSEDED (and records when).
                    IF OLD.status = 'ACTIVE'
                       AND NOT (
                           NEW.status = 'SUPERSEDED'
                           AND to_jsonb(NEW) - ARRAY[
                                   'status', 'superseded_at', 'updated_at',
                                   'lock_version'
                               ]
                               = to_jsonb(OLD) - ARRAY[
                                   'status', 'superseded_at', 'updated_at',
                                   'lock_version'
                               ]
                       )
                    THEN
                        RAISE EXCEPTION
                            'care.treatment_plans: ACTIVE only becomes SUPERSEDED'
                            USING HINT = 'Create a new plan version instead.';
                    END IF;
                    RETURN NEW;
                END IF;

                -- Items: the plan must be DRAFT (before and after the change).
                IF TG_OP = 'INSERT' THEN
                    plan_ids := ARRAY[NEW.plan_id];
                ELSIF TG_OP = 'DELETE' THEN
                    plan_ids := ARRAY[OLD.plan_id];
                ELSE
                    plan_ids := ARRAY[OLD.plan_id, NEW.plan_id];
                END IF;
                FOREACH plan_id IN ARRAY plan_ids
                LOOP
                    SELECT p.status INTO plan_status
                      FROM care.treatment_plans AS p WHERE p.id = plan_id;
                    IF plan_status IS DISTINCT FROM 'DRAFT' THEN
                        RAISE EXCEPTION
                            'care.treatment_plan_items: the plan is not DRAFT'
                            USING HINT = 'Items change only while the plan is DRAFT.';
                    END IF;
                END LOOP;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END
            $$;
        COMMENT ON FUNCTION care.guard_plan_items_draft() IS
            'Plan items change only in DRAFT; an ACTIVE plan only becomes SUPERSEDED';

        CREATE TRIGGER tg_treatment_plans_guard
            BEFORE UPDATE ON care.treatment_plans
            FOR EACH ROW EXECUTE FUNCTION care.guard_plan_items_draft();
        CREATE TRIGGER tg_treatment_plan_items_guard
            BEFORE INSERT OR UPDATE OR DELETE ON care.treatment_plan_items
            FOR EACH ROW EXECUTE FUNCTION care.guard_plan_items_draft();
        """
    )
    op.execute(
        "\n".join(
            f"CREATE TRIGGER tg_{table}_append_only\n"
            f"    BEFORE UPDATE OR DELETE ON care.{table}\n"
            f"    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();"
            for table in APPEND_ONLY
        )
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
            for table in RLS_TABLES
        )
    )

    # Catalogs (RLS-3): global rows and the session clinic; CLINIC_ADMIN writes
    # its own clinic; the migrator seeds.
    all_roles = "('CLINIC_ADMIN', 'DOCTOR', 'PATIENT', 'SYSTEM')"
    staff_roles = "('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')"
    catalogs = (
        ("target_profiles", "target_profiles", all_roles),
        ("protocol_templates", "protocol_templates", staff_roles),
        ("reading_validation_settings", "reading_validation_settings", all_roles),
        ("symptom_catalog", "symptom_catalog", all_roles),
    )
    for table, prefix, roles in catalogs:
        op.execute(
            f"""
            CREATE POLICY {prefix}_select ON care.{table} FOR SELECT
                USING ({SEED_READ.format(roles=roles)});
            CREATE POLICY {prefix}_insert_clinic_admin ON care.{table}
                FOR INSERT WITH CHECK ({ADMIN_ONLY});
            CREATE POLICY {prefix}_update_clinic_admin ON care.{table}
                FOR UPDATE USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY});
            CREATE POLICY {prefix}_migrator ON care.{table}
                FOR ALL TO insumap_migrator USING (true) WITH CHECK (true);
            """
        )

    # Clinical tables (RLS-1 and RLS-2): DOCTOR and SYSTEM in the clinic,
    # PATIENT only its own rows where the table is visible to it.
    op.execute(
        f"""
        CREATE POLICY patient_target_assignments_select
            ON care.patient_target_assignments FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY patient_target_assignments_insert_doctor
            ON care.patient_target_assignments FOR INSERT
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'DOCTOR');
        CREATE POLICY patient_target_assignments_update_doctor
            ON care.patient_target_assignments FOR UPDATE
            USING ({IN_CLINIC} AND iam.app_role() = 'DOCTOR')
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'DOCTOR');

        CREATE POLICY treatment_plans_select ON care.treatment_plans
            FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR ({OWN_PATIENT} AND status = 'ACTIVE')
                )
            );
        CREATE POLICY treatment_plans_insert_staff ON care.treatment_plans
            FOR INSERT WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY treatment_plans_update_staff ON care.treatment_plans
            FOR UPDATE USING ({IN_CLINIC} AND {CLINICAL_STAFF})
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY treatment_plan_items_select ON care.treatment_plan_items
            FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND plan_id IN (SELECT p.id FROM care.treatment_plans AS p)
                    )
                )
            );
        CREATE POLICY treatment_plan_items_insert_staff
            ON care.treatment_plan_items FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY treatment_plan_items_update_staff
            ON care.treatment_plan_items FOR UPDATE
            USING ({IN_CLINIC} AND {CLINICAL_STAFF})
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY treatment_plan_items_delete_staff
            ON care.treatment_plan_items FOR DELETE
            USING ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY treatment_plan_snapshots_select
            ON care.treatment_plan_snapshots FOR SELECT
            USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY treatment_plan_snapshots_insert
            ON care.treatment_plan_snapshots FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY reminder_schedules_select ON care.reminder_schedules
            FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY reminder_schedules_insert_doctor
            ON care.reminder_schedules FOR INSERT
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'DOCTOR');
        CREATE POLICY reminder_schedules_update ON care.reminder_schedules
            FOR UPDATE
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})))
            WITH CHECK ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));

        CREATE POLICY follow_ups_select ON care.follow_ups FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (iam.app_role() = 'CLINIC_ADMIN' AND kind = 'OUTREACH')
                    OR ({OWN_PATIENT} AND kind = 'CONTROL')
                )
            );
        CREATE POLICY follow_ups_insert ON care.follow_ups FOR INSERT
            WITH CHECK (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (iam.app_role() = 'CLINIC_ADMIN' AND kind = 'OUTREACH')
                )
            );
        CREATE POLICY follow_ups_update ON care.follow_ups FOR UPDATE
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (iam.app_role() = 'CLINIC_ADMIN' AND kind = 'OUTREACH')
                )
            )
            WITH CHECK (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (iam.app_role() = 'CLINIC_ADMIN' AND kind = 'OUTREACH')
                )
            );

        CREATE POLICY clinical_notes_select ON care.clinical_notes FOR SELECT
            USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY clinical_notes_insert ON care.clinical_notes FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY triage_reviews_select ON care.triage_reviews FOR SELECT
            USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY triage_reviews_insert ON care.triage_reviews FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY glycemic_metric_snapshots_select
            ON care.glycemic_metric_snapshots FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY glycemic_metric_snapshots_insert
            ON care.glycemic_metric_snapshots FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});
        """
    )

    # insumap_app: minimum DML per table. Only the items of a DRAFT plan can be
    # deleted (the trigger and the policy keep it so).
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON care.target_profiles,
            care.patient_target_assignments, care.protocol_templates,
            care.treatment_plans, care.reminder_schedules, care.follow_ups,
            care.reading_validation_settings, care.symptom_catalog
            TO insumap_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON care.treatment_plan_items
            TO insumap_app;
        GRANT SELECT, INSERT ON care.treatment_plan_snapshots,
            care.clinical_notes, care.triage_reviews,
            care.glycemic_metric_snapshots
            TO insumap_app;
        """
    )

    op.execute(f"GRANT SELECT ON {', '.join(RLS_TABLES)} TO insumap_readonly;")


def downgrade() -> None:
    """Revoke the privileges and drop the care schema with its functions."""
    op.execute(
        f"REVOKE ALL ON {', '.join(RLS_TABLES)} FROM insumap_app, insumap_readonly;"
    )
    op.execute(
        "\n".join(
            f"DROP POLICY {policy} ON {table};" for policy, table in reversed(POLICIES)
        )
    )
    op.execute(
        """
        DROP TABLE care.glycemic_metric_snapshots;
        DROP TABLE care.symptom_catalog;
        DROP TABLE care.reading_validation_settings;
        DROP TABLE care.triage_reviews;
        DROP TABLE care.clinical_notes;
        DROP TABLE care.follow_ups;
        DROP TABLE care.reminder_schedules;
        DROP TABLE care.treatment_plan_snapshots;
        DROP TABLE care.treatment_plan_items;
        DROP TABLE care.treatment_plans;
        DROP TABLE care.protocol_templates;
        DROP TABLE care.patient_target_assignments;
        DROP TABLE care.target_profiles;
        DROP FUNCTION care.guard_plan_items_draft();
        DROP FUNCTION care.reject_active_version_update();
        REVOKE USAGE ON SCHEMA care
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP SCHEMA care;
        """
    )
