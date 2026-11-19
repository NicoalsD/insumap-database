"""Create the monitoring schema: append-only patient records.

Modelo de datos 9.4, Diccionario section 7. Eight tables: sync_batches,
glucose_readings, reading_issues, insulin_doses, meals, physical_activities,
symptoms and lab_results.

Every table is append-only (audit.reject_mutation() on UPDATE and DELETE, for
all roles) and insumap_app has neither UPDATE nor DELETE on them. A correction
or a void is a new row with entry_type CORRECTION or VOID that points to the
original through supersedes_id and carries a reason. The current version of a
reading is the view monitoring.current_glucose_readings (security_invoker, so
the RLS of the caller applies).

The doc CHECK (entry_type = 'ORIGINAL') = (supersedes_id IS NULL AND
correction_reason IS NULL) still lets a correction carry only one of the two, so
ck_<table>_correction_complete requires both, as the Diccionario describes.

Records reference their sync batch with a DEFERRABLE INITIALLY DEFERRED foreign
key, so the batch is inserted last with its final counts (Modelo de datos 7).

Row level security (RLS-2, Modelo de datos 4.3 to 4.5): a PATIENT inserts and
reads only its own rows; DOCTOR and SYSTEM read the session clinic (the doctor
to patient assignment is checked by PatientRecordAccessProxy); a DOCTOR may also
insert lab_results (labs.write_assigned). There is no branch for CLINIC_ADMIN or
PLATFORM_ADMIN. Inserts also require recorded_by (submitted_by for batches) to
be the session user. reading_issues has no patient column: its policies go
through the readings the caller can see.

Revision ID: 6501d51b9a95
Revises: 5300cb16e9a8
Create Date: 2026-11-19 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "6501d51b9a95"
down_revision: str | None = "5300cb16e9a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECORD_TABLES = (
    "glucose_readings",
    "insulin_doses",
    "meals",
    "physical_activities",
    "symptoms",
    "lab_results",
)
ALL_TABLES = ("sync_batches", *RECORD_TABLES, "reading_issues")

IN_CLINIC = "clinic_id = iam.app_clinic_id()"
OWN_PATIENT = "iam.app_role() = 'PATIENT' AND patient_id = clinic.current_patient_id()"
CLINICAL_STAFF = "iam.app_role() IN ('DOCTOR', 'SYSTEM')"


def record_table(table: str, head: str, checks: str) -> str:
    """Return the CREATE TABLE of a record table.

    head holds the columns specific to the table; the identity columns and the
    correction columns are the same in every record table (Diccionario 1).
    """
    return f"""
        CREATE TABLE monitoring.{table} (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            {head},
            recorded_by uuid NOT NULL,
            sync_batch_id uuid,
            received_at timestamptz NOT NULL DEFAULT now(),
            entry_type varchar(10) NOT NULL DEFAULT 'ORIGINAL',
            supersedes_id uuid,
            correction_reason varchar(500),
            CONSTRAINT pk_{table} PRIMARY KEY (id),
            CONSTRAINT fk_{table}_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_{table}_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_{table}_recorded_by FOREIGN KEY (recorded_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_{table}_sync_batch_id FOREIGN KEY (sync_batch_id)
                REFERENCES monitoring.sync_batches (id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_{table}_supersedes_id FOREIGN KEY (supersedes_id)
                REFERENCES monitoring.{table} (id) ON DELETE RESTRICT,
            CONSTRAINT ck_{table}_entry_type CHECK (
                entry_type IN ('ORIGINAL', 'CORRECTION', 'VOID')
            ),
            CONSTRAINT ck_{table}_correction CHECK (
                (entry_type = 'ORIGINAL')
                = (supersedes_id IS NULL AND correction_reason IS NULL)
            ),
            CONSTRAINT ck_{table}_correction_complete CHECK (
                entry_type = 'ORIGINAL'
                OR (supersedes_id IS NOT NULL AND correction_reason IS NOT NULL)
            ),
            CONSTRAINT ck_{table}_correction_reason_length CHECK (
                char_length(correction_reason) BETWEEN 10 AND 500
            ),
            {checks}
        );
        CREATE INDEX ix_{table}_supersedes
            ON monitoring.{table} (supersedes_id) WHERE supersedes_id IS NOT NULL;
        """


def upgrade() -> None:
    """Create the schema, tables, triggers, view, policies and grants."""
    op.execute(
        """
        CREATE SCHEMA monitoring;
        COMMENT ON SCHEMA monitoring IS 'Patient records (append-only)';
        GRANT USAGE ON SCHEMA monitoring
            TO insumap_app, insumap_readonly, insumap_definer;

        CREATE TABLE monitoring.sync_batches (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            source varchar(13) NOT NULL,
            submitted_by uuid NOT NULL,
            total_items integer NOT NULL,
            created_count integer NOT NULL,
            duplicate_count integer NOT NULL,
            rejected_count integer NOT NULL,
            file_sha256 char(64),
            received_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_sync_batches PRIMARY KEY (id),
            CONSTRAINT fk_sync_batches_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_sync_batches_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_sync_batches_submitted_by FOREIGN KEY (submitted_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_sync_batches_source CHECK (
                source IN ('OFFLINE_QUEUE', 'CGM_CSV', 'SIMULATOR')
            ),
            CONSTRAINT ck_sync_batches_total_items CHECK (total_items >= 0),
            CONSTRAINT ck_sync_batches_created_count CHECK (created_count >= 0),
            CONSTRAINT ck_sync_batches_duplicate_count CHECK (duplicate_count >= 0),
            CONSTRAINT ck_sync_batches_rejected_count CHECK (rejected_count >= 0),
            CONSTRAINT ck_sync_batches_counts CHECK (
                created_count + duplicate_count + rejected_count = total_items
            )
        );
        CREATE INDEX ix_sync_batches_patient_received
            ON monitoring.sync_batches (patient_id, received_at DESC);
        COMMENT ON TABLE monitoring.sync_batches IS
            'Batch of records (offline queue, CGM CSV or simulator); written once';
        """
    )

    op.execute(
        record_table(
            "glucose_readings",
            """client_reading_id uuid NOT NULL,
            measured_at timestamptz NOT NULL,
            value_mg_dl numeric(5,1) NOT NULL,
            source varchar(6) NOT NULL,
            context varchar(10),
            note varchar(500),
            status varchar(8) NOT NULL DEFAULT 'ACCEPTED'""",
            """CONSTRAINT ck_glucose_readings_value_mg_dl CHECK (value_mg_dl > 0),
            CONSTRAINT ck_glucose_readings_source CHECK (
                source IN ('CGM', 'SMBG', 'MANUAL')
            ),
            CONSTRAINT ck_glucose_readings_context CHECK (
                context IN (
                    'FASTING', 'PRE_MEAL', 'POST_MEAL', 'BEDTIME', 'EXERCISE',
                    'OTHER'
                )
            ),
            CONSTRAINT ck_glucose_readings_status CHECK (
                status IN ('ACCEPTED', 'FLAGGED')
            )""",
        )
        + """
        CREATE UNIQUE INDEX ux_glucose_readings_client_id
            ON monitoring.glucose_readings (patient_id, client_reading_id);
        CREATE INDEX ix_glucose_readings_patient_measured
            ON monitoring.glucose_readings (patient_id, measured_at DESC)
            INCLUDE (value_mg_dl, source, status, entry_type);
        CREATE INDEX ix_glucose_readings_clinic_measured
            ON monitoring.glucose_readings (clinic_id, measured_at);
        COMMENT ON TABLE monitoring.glucose_readings IS
            'Glucose in mg/dL; suspicious readings are flagged, never hidden';

        CREATE VIEW monitoring.current_glucose_readings
            WITH (security_invoker = true) AS
            SELECT r.*
              FROM monitoring.glucose_readings AS r
             WHERE r.entry_type <> 'VOID'
               AND NOT EXISTS (
                   SELECT 1 FROM monitoring.glucose_readings AS s
                    WHERE s.supersedes_id = r.id
               );
        COMMENT ON VIEW monitoring.current_glucose_readings IS
            'Current version of each reading: not superseded and not a void';

        CREATE TABLE monitoring.reading_issues (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            reading_id uuid NOT NULL,
            issue_code varchar(32) NOT NULL,
            detail jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reading_issues PRIMARY KEY (id),
            CONSTRAINT fk_reading_issues_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_reading_issues_reading_id FOREIGN KEY (reading_id)
                REFERENCES monitoring.glucose_readings (id) ON DELETE RESTRICT,
            CONSTRAINT ck_reading_issues_issue_code CHECK (
                issue_code IN (
                    'DUPLICATE_SUSPECTED', 'IMPLAUSIBLE_RATE_OF_CHANGE',
                    'UNIT_MISMATCH_SUSPECTED'
                )
            ),
            CONSTRAINT ck_reading_issues_detail CHECK (
                jsonb_typeof(detail) = 'object'
            )
        );
        CREATE UNIQUE INDEX ux_reading_issues_reading_code
            ON monitoring.reading_issues (reading_id, issue_code);
        COMMENT ON TABLE monitoring.reading_issues IS
            'Flags the validation chain (P12) left on a reading; one row per flag';
        """
    )

    op.execute(
        record_table(
            "insulin_doses",
            """client_record_id uuid NOT NULL,
            administered_at timestamptz NOT NULL,
            insulin_kind varchar(10) NOT NULL,
            product_name varchar(80) NOT NULL,
            units numeric(6,2) NOT NULL,
            plan_id uuid,
            source varchar(6) NOT NULL DEFAULT 'MANUAL',
            note varchar(500)""",
            """CONSTRAINT fk_insulin_doses_plan_id FOREIGN KEY (plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT ck_insulin_doses_insulin_kind CHECK (
                insulin_kind IN ('BASAL', 'BOLUS', 'CORRECTION', 'MIXED')
            ),
            CONSTRAINT ck_insulin_doses_units CHECK (units > 0),
            CONSTRAINT ck_insulin_doses_source CHECK (
                source IN ('MANUAL', 'IMPORT')
            )""",
        )
        + """
        CREATE UNIQUE INDEX ux_insulin_doses_client_id
            ON monitoring.insulin_doses (patient_id, client_record_id);
        CREATE INDEX ix_insulin_doses_patient_time
            ON monitoring.insulin_doses (patient_id, administered_at DESC);
        COMMENT ON TABLE monitoring.insulin_doses IS
            'Doses recorded by the patient, never suggested by the app';
        """
    )

    op.execute(
        record_table(
            "meals",
            """client_record_id uuid NOT NULL,
            eaten_at timestamptz NOT NULL,
            meal_type varchar(9) NOT NULL,
            carbs_g numeric(5,1) NOT NULL,
            note varchar(500)""",
            """CONSTRAINT ck_meals_meal_type CHECK (
                meal_type IN ('BREAKFAST', 'LUNCH', 'DINNER', 'SNACK')
            ),
            CONSTRAINT ck_meals_carbs_g CHECK (carbs_g >= 0)""",
        )
        + """
        CREATE UNIQUE INDEX ux_meals_client_id
            ON monitoring.meals (patient_id, client_record_id);
        CREATE INDEX ix_meals_patient_time
            ON monitoring.meals (patient_id, eaten_at DESC);
        COMMENT ON TABLE monitoring.meals IS 'Meals with estimated carbohydrates';
        """
    )

    op.execute(
        record_table(
            "physical_activities",
            """client_record_id uuid NOT NULL,
            started_at timestamptz NOT NULL,
            activity_type varchar(40) NOT NULL,
            duration_min smallint NOT NULL,
            intensity varchar(8) NOT NULL,
            note varchar(500)""",
            """CONSTRAINT ck_physical_activities_duration_min CHECK (
                duration_min > 0
            ),
            CONSTRAINT ck_physical_activities_intensity CHECK (
                intensity IN ('LOW', 'MODERATE', 'HIGH')
            )""",
        )
        + """
        CREATE UNIQUE INDEX ux_physical_activities_client_id
            ON monitoring.physical_activities (patient_id, client_record_id);
        CREATE INDEX ix_physical_activities_patient_time
            ON monitoring.physical_activities (patient_id, started_at DESC);
        COMMENT ON TABLE monitoring.physical_activities IS 'Physical activity';
        """
    )

    op.execute(
        record_table(
            "symptoms",
            """client_record_id uuid NOT NULL,
            occurred_at timestamptz NOT NULL,
            symptom_id uuid NOT NULL,
            note varchar(500)""",
            """CONSTRAINT fk_symptoms_symptom_id FOREIGN KEY (symptom_id)
                REFERENCES care.symptom_catalog (id) ON DELETE RESTRICT""",
        )
        + """
        CREATE UNIQUE INDEX ux_symptoms_client_id
            ON monitoring.symptoms (patient_id, client_record_id);
        CREATE INDEX ix_symptoms_patient_time
            ON monitoring.symptoms (patient_id, occurred_at DESC);
        COMMENT ON TABLE monitoring.symptoms IS
            'Symptoms recorded from the care.symptom_catalog';
        """
    )

    op.execute(
        record_table(
            "lab_results",
            """client_record_id uuid NOT NULL,
            test_code varchar(8) NOT NULL DEFAULT 'HBA1C',
            performed_on date NOT NULL,
            value numeric(6,2) NOT NULL,
            unit varchar(9) NOT NULL,
            value_pct numeric(4,1) NOT NULL,
            origin varchar(16) NOT NULL""",
            """CONSTRAINT ck_lab_results_test_code CHECK (test_code IN ('HBA1C')),
            CONSTRAINT ck_lab_results_value CHECK (value > 0),
            CONSTRAINT ck_lab_results_unit CHECK (unit IN ('%', 'mmol/mol')),
            CONSTRAINT ck_lab_results_value_pct CHECK (value_pct > 0),
            CONSTRAINT ck_lab_results_origin CHECK (
                origin IN ('PATIENT_REPORTED', 'LAB')
            )""",
        )
        + """
        CREATE UNIQUE INDEX ux_lab_results_client_id
            ON monitoring.lab_results (patient_id, client_record_id);
        CREATE INDEX ix_lab_results_patient_date
            ON monitoring.lab_results (patient_id, performed_on DESC);
        COMMENT ON TABLE monitoring.lab_results IS
            'HbA1c (LOINC 4548-4), self-reported or confirmed by the doctor';
        COMMENT ON COLUMN monitoring.lab_results.value_pct IS
            'Value normalized to percent (NGSP)';
        """
    )

    op.execute(
        "\n".join(
            f"CREATE TRIGGER tg_{table}_append_only\n"
            f"    BEFORE UPDATE OR DELETE ON monitoring.{table}\n"
            f"    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();"
            for table in ALL_TABLES
        )
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE monitoring.{table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE monitoring.{table} FORCE ROW LEVEL SECURITY;"
            for table in ALL_TABLES
        )
    )

    # The record tables and the batches share the same shape of policy.
    for table in ("sync_batches", *RECORD_TABLES):
        author = "submitted_by" if table == "sync_batches" else "recorded_by"
        op.execute(
            f"""
            CREATE POLICY {table}_select ON monitoring.{table} FOR SELECT
                USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
            CREATE POLICY {table}_insert_own ON monitoring.{table} FOR INSERT
                WITH CHECK (
                    {IN_CLINIC} AND {OWN_PATIENT}
                    AND {author} = iam.app_user_id()
                );
            """
        )
    op.execute(
        f"""
        CREATE POLICY lab_results_insert_doctor ON monitoring.lab_results
            FOR INSERT
            WITH CHECK (
                {IN_CLINIC} AND iam.app_role() = 'DOCTOR'
                AND recorded_by = iam.app_user_id()
            );

        CREATE POLICY reading_issues_select ON monitoring.reading_issues
            FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND reading_id IN (
                            SELECT r.id FROM monitoring.glucose_readings AS r
                        )
                    )
                )
            );
        CREATE POLICY reading_issues_insert_own ON monitoring.reading_issues
            FOR INSERT
            WITH CHECK (
                {IN_CLINIC}
                AND iam.app_role() = 'PATIENT'
                AND reading_id IN (
                    SELECT r.id FROM monitoring.glucose_readings AS r
                )
            );
        """
    )

    tables = ", ".join(f"monitoring.{table}" for table in ALL_TABLES)
    op.execute(
        f"""
        GRANT SELECT, INSERT ON {tables} TO insumap_app;
        GRANT SELECT ON monitoring.current_glucose_readings TO insumap_app;
        GRANT SELECT ON {tables} TO insumap_readonly;
        GRANT SELECT ON monitoring.current_glucose_readings TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the monitoring schema (its policies and privileges go with it)."""
    op.execute(
        """
        DROP VIEW monitoring.current_glucose_readings;
        DROP TABLE monitoring.reading_issues;
        DROP TABLE monitoring.lab_results;
        DROP TABLE monitoring.symptoms;
        DROP TABLE monitoring.physical_activities;
        DROP TABLE monitoring.meals;
        DROP TABLE monitoring.insulin_doses;
        DROP TABLE monitoring.glucose_readings;
        DROP TABLE monitoring.sync_batches;
        REVOKE USAGE ON SCHEMA monitoring
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP SCHEMA monitoring;
        """
    )
