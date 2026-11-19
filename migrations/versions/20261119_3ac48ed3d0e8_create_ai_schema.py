"""Create the ai schema: forecasts, risk, recommendations and the assistant.

Modelo de datos 9.6, Diccionario section 9. Ten tables: model_versions,
glucose_forecasts, risk_assessments, pattern_findings, recommendations,
recommendation_decisions, clinical_summaries, assistant_messages,
knowledge_documents and ai_evaluations.

Every output of the AI keeps the model name and version, the SHA-256 of its
canonical input, the explanation for the doctor, and whether it came from the
fallback (with the reason) or from a model in shadow mode.

This revision also closes the circular references the earlier schemas could not
create yet: care.treatment_plans.source_recommendation_id, care.follow_ups and
care.triage_reviews (risk_assessment_id) and alerts.alerts.forecast_id.

Append-only (audit.reject_mutation()): glucose_forecasts, risk_assessments,
pattern_findings, clinical_summaries, assistant_messages and
recommendation_decisions. recommendations is mutable (its status moves) and
requires_doctor_approval is always true: the AI never applies a change alone.

knowledge_documents uses pg_trgm for the keyword search of the assistant. The
migrator cannot create objects in public (PostgreSQL 15 and later), so the
extension lives in the extensions schema, unless it is already installed.

Row level security (Modelo de datos 4.3 to 4.5): DOCTOR and SYSTEM work on the
session clinic and CLINIC_ADMIN has no access to the outputs, except for
knowledge_documents (which it manages) and the evaluations (read). A PATIENT
reads its own forecasts and writes and reads its own assistant conversation.
model_versions has no clinic_id and no RLS (RLS-0 with write): it holds no
personal data.

Revision ID: 3ac48ed3d0e8
Revises: 9825fa46b3be
Create Date: 2026-11-19 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "3ac48ed3d0e8"
down_revision: str | None = "9825fa46b3be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables with clinic_id (model_versions has none and no RLS).
RLS_TABLES = (
    "glucose_forecasts",
    "risk_assessments",
    "pattern_findings",
    "recommendations",
    "recommendation_decisions",
    "clinical_summaries",
    "assistant_messages",
    "knowledge_documents",
    "ai_evaluations",
)
APPEND_ONLY = (
    "glucose_forecasts",
    "risk_assessments",
    "pattern_findings",
    "clinical_summaries",
    "assistant_messages",
    "recommendation_decisions",
)

IN_CLINIC = "clinic_id = iam.app_clinic_id()"
OWN_PATIENT = "iam.app_role() = 'PATIENT' AND patient_id = clinic.current_patient_id()"
CLINICAL_STAFF = "iam.app_role() IN ('DOCTOR', 'SYSTEM')"
ADMIN_ONLY = f"{IN_CLINIC} AND iam.app_role() = 'CLINIC_ADMIN'"
FALLBACK_REASONS = "'AI_TIMEOUT', 'CIRCUIT_OPEN', 'AI_ERROR', 'INSUFFICIENT_DATA'"


def output_columns() -> str:
    """Return the columns every AI output repeats (Diccionario 9)."""
    return """model_name varchar(80) NOT NULL,
            model_version varchar(80) NOT NULL,
            input_hash char(64) NOT NULL,
            explanation jsonb NOT NULL,
            is_fallback boolean NOT NULL DEFAULT false,
            fallback_reason varchar(17),
            is_shadow boolean NOT NULL DEFAULT false,
            generated_at timestamptz NOT NULL"""


def output_checks(table: str) -> str:
    """Return the constraints every AI output repeats."""
    return f"""CONSTRAINT ck_{table}_explanation CHECK (
                jsonb_typeof(explanation) = 'object'
            ),
            CONSTRAINT ck_{table}_fallback_reason CHECK (
                fallback_reason IN ({FALLBACK_REASONS})
            ),
            CONSTRAINT ck_{table}_fallback CHECK (
                is_fallback = (fallback_reason IS NOT NULL)
            )"""


def clinic_patient_fks(table: str) -> str:
    """Return the foreign keys to the clinic and the patient."""
    return f"""CONSTRAINT fk_{table}_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_{table}_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT"""


def upgrade() -> None:
    """Create the schema, tables, foreign keys, triggers, policies and grants."""
    op.execute(
        """
        CREATE SCHEMA ai;
        COMMENT ON SCHEMA ai IS 'AI outputs and decision support';
        GRANT USAGE ON SCHEMA ai
            TO insumap_app, insumap_readonly, insumap_definer;

        CREATE TABLE ai.model_versions (
            id uuid NOT NULL DEFAULT uuidv7(),
            use_case varchar(3) NOT NULL,
            model_name varchar(80) NOT NULL,
            model_version varchar(80) NOT NULL,
            simulator_version varchar(40),
            training_seed integer,
            validation_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(7) NOT NULL DEFAULT 'SHADOW',
            activated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_model_versions PRIMARY KEY (id),
            CONSTRAINT ck_model_versions_use_case CHECK (
                use_case IN ('IA1', 'IA2', 'IA3', 'IA4', 'IA5', 'IA6', 'IA7')
            ),
            CONSTRAINT ck_model_versions_validation_metrics CHECK (
                jsonb_typeof(validation_metrics) = 'object'
            ),
            CONSTRAINT ck_model_versions_status CHECK (
                status IN ('SHADOW', 'ACTIVE', 'RETIRED')
            )
        );
        CREATE UNIQUE INDEX ux_model_versions_name_version
            ON ai.model_versions (model_name, model_version);
        COMMENT ON TABLE ai.model_versions IS
            'Deployed models: version, seed, simulator version and validation';
        """
    )

    op.execute(
        f"""
        CREATE TABLE ai.glucose_forecasts (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            mode varchar(10) NOT NULL,
            based_on_reading_id uuid,
            points jsonb NOT NULL,
            hypo_probability jsonb NOT NULL DEFAULT '[]'::jsonb,
            hyper_probability jsonb NOT NULL DEFAULT '[]'::jsonb,
            target_profile_id uuid NOT NULL,
            {output_columns()},
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_glucose_forecasts PRIMARY KEY (id),
            {clinic_patient_fks("glucose_forecasts")},
            CONSTRAINT fk_glucose_forecasts_based_on_reading_id
                FOREIGN KEY (based_on_reading_id)
                REFERENCES monitoring.glucose_readings (id) ON DELETE RESTRICT,
            CONSTRAINT fk_glucose_forecasts_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT ck_glucose_forecasts_mode CHECK (
                mode IN ('SHORT_TERM', 'OVERNIGHT')
            ),
            CONSTRAINT ck_glucose_forecasts_points CHECK (
                jsonb_typeof(points) = 'array'
            ),
            CONSTRAINT ck_glucose_forecasts_hypo_probability CHECK (
                jsonb_typeof(hypo_probability) = 'array'
            ),
            CONSTRAINT ck_glucose_forecasts_hyper_probability CHECK (
                jsonb_typeof(hyper_probability) = 'array'
            ),
            {output_checks("glucose_forecasts")}
        );
        CREATE INDEX ix_glucose_forecasts_latest
            ON ai.glucose_forecasts (patient_id, generated_at DESC)
            WHERE NOT is_shadow;
        CREATE INDEX ix_glucose_forecasts_input_hash
            ON ai.glucose_forecasts (patient_id, input_hash);
        COMMENT ON TABLE ai.glucose_forecasts IS
            'IA1 forecasts with p10, p50 and p90 and hypo and hyper risk';

        CREATE TABLE ai.risk_assessments (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            assessment_type varchar(13) NOT NULL,
            risk_probability numeric(5,4),
            risk_tier varchar(8),
            calibrated boolean NOT NULL DEFAULT true,
            window_days smallint NOT NULL,
            metric_snapshot_id uuid,
            target_profile_id uuid NOT NULL,
            {output_columns()},
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_risk_assessments PRIMARY KEY (id),
            {clinic_patient_fks("risk_assessments")},
            CONSTRAINT fk_risk_assessments_metric_snapshot_id
                FOREIGN KEY (metric_snapshot_id)
                REFERENCES care.glycemic_metric_snapshots (id) ON DELETE RESTRICT,
            CONSTRAINT fk_risk_assessments_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT ck_risk_assessments_assessment_type CHECK (
                assessment_type IN ('DETERIORATION', 'DROPOUT')
            ),
            CONSTRAINT ck_risk_assessments_risk_probability CHECK (
                risk_probability BETWEEN 0 AND 1
            ),
            CONSTRAINT ck_risk_assessments_risk_tier CHECK (
                risk_tier IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
            ),
            CONSTRAINT ck_risk_assessments_window_days CHECK (window_days > 0),
            {output_checks("risk_assessments")}
        );
        CREATE INDEX ix_risk_assessments_latest
            ON ai.risk_assessments (patient_id, assessment_type, generated_at DESC)
            WHERE NOT is_shadow;
        CREATE INDEX ix_risk_assessments_triage
            ON ai.risk_assessments
            (clinic_id, assessment_type, risk_tier, generated_at DESC);
        COMMENT ON TABLE ai.risk_assessments IS
            'Risk of glycemic deterioration (IA2) and of dropout (IA7)';

        CREATE TABLE ai.pattern_findings (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            pattern_type varchar(24) NOT NULL,
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            confidence numeric(5,4),
            evidence jsonb NOT NULL,
            {output_columns()},
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pattern_findings PRIMARY KEY (id),
            {clinic_patient_fks("pattern_findings")},
            CONSTRAINT ck_pattern_findings_pattern_type CHECK (
                pattern_type IN (
                    'NOCTURNAL_HYPOGLYCEMIA', 'DAWN_PHENOMENON',
                    'POSTPRANDIAL_SPIKE', 'MISSED_DOSE', 'DUPLICATE_DOSE',
                    'ADHERENCE_DROP', 'IMPLAUSIBLE_READING'
                )
            ),
            CONSTRAINT ck_pattern_findings_window CHECK (
                window_end > window_start
            ),
            CONSTRAINT ck_pattern_findings_confidence CHECK (
                confidence BETWEEN 0 AND 1
            ),
            CONSTRAINT ck_pattern_findings_evidence CHECK (
                jsonb_typeof(evidence) = 'object'
            ),
            {output_checks("pattern_findings")}
        );
        CREATE INDEX ix_pattern_findings_patient
            ON ai.pattern_findings (patient_id, generated_at DESC)
            WHERE NOT is_shadow;
        COMMENT ON TABLE ai.pattern_findings IS
            'IA3 patterns and anomalies with window, evidence and confidence';
        """
    )

    op.execute(
        f"""
        CREATE TABLE ai.recommendations (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            plan_id uuid NOT NULL,
            applied_plan_id uuid,
            status varchar(21) NOT NULL DEFAULT 'PROPOSED',
            action varchar(8) NOT NULL,
            suggestion jsonb NOT NULL,
            requires_doctor_approval boolean NOT NULL DEFAULT true,
            target_profile_id uuid NOT NULL,
            proposed_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            {output_columns()},
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_recommendations PRIMARY KEY (id),
            {clinic_patient_fks("recommendations")},
            CONSTRAINT fk_recommendations_plan_id FOREIGN KEY (plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT fk_recommendations_applied_plan_id
                FOREIGN KEY (applied_plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT fk_recommendations_target_profile_id
                FOREIGN KEY (target_profile_id)
                REFERENCES care.target_profiles (id) ON DELETE RESTRICT,
            CONSTRAINT ck_recommendations_status CHECK (
                status IN (
                    'PROPOSED', 'APPROVED', 'APPROVED_WITH_CHANGES', 'REJECTED',
                    'APPLIED', 'EXPIRED'
                )
            ),
            CONSTRAINT ck_recommendations_action CHECK (
                action IN ('INCREASE', 'DECREASE')
            ),
            CONSTRAINT ck_recommendations_suggestion CHECK (
                jsonb_typeof(suggestion) = 'object'
            ),
            CONSTRAINT ck_recommendations_requires_doctor_approval CHECK (
                requires_doctor_approval
            ),
            CONSTRAINT ck_recommendations_expires_at CHECK (expires_at > proposed_at),
            CONSTRAINT ck_recommendations_lock_version CHECK (lock_version >= 0),
            {output_checks("recommendations")}
        );
        CREATE UNIQUE INDEX ux_recommendations_pending
            ON ai.recommendations (plan_id, action) WHERE status = 'PROPOSED';
        CREATE INDEX ix_recommendations_patient_status
            ON ai.recommendations (patient_id, status, proposed_at DESC);
        CREATE INDEX ix_recommendations_expiry
            ON ai.recommendations (expires_at) WHERE status = 'PROPOSED';
        COMMENT ON TABLE ai.recommendations IS
            'IA4 treatment adjustment proposals; the doctor always decides';

        CREATE TABLE ai.recommendation_decisions (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            recommendation_id uuid NOT NULL,
            decision varchar(21) NOT NULL,
            decided_by uuid,
            decided_at timestamptz NOT NULL,
            reason varchar(500),
            reason_code varchar(40),
            original_suggestion jsonb,
            modified_suggestion jsonb,
            model_version varchar(80) NOT NULL,
            applied_plan_id uuid,
            CONSTRAINT pk_recommendation_decisions PRIMARY KEY (id),
            CONSTRAINT fk_recommendation_decisions_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_recommendation_decisions_recommendation_id
                FOREIGN KEY (recommendation_id)
                REFERENCES ai.recommendations (id) ON DELETE RESTRICT,
            CONSTRAINT fk_recommendation_decisions_decided_by
                FOREIGN KEY (decided_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_recommendation_decisions_applied_plan_id
                FOREIGN KEY (applied_plan_id)
                REFERENCES care.treatment_plans (id) ON DELETE RESTRICT,
            CONSTRAINT ck_recommendation_decisions_decision CHECK (
                decision IN (
                    'APPROVED', 'APPROVED_WITH_CHANGES', 'REJECTED', 'APPLIED',
                    'EXPIRED'
                )
            ),
            CONSTRAINT ck_recommendation_decisions_reason_length CHECK (
                char_length(reason) BETWEEN 10 AND 500
            ),
            CONSTRAINT ck_recommendation_decisions_reason_required CHECK (
                decision NOT IN ('APPROVED_WITH_CHANGES', 'REJECTED')
                OR reason IS NOT NULL
            ),
            CONSTRAINT ck_recommendation_decisions_original_suggestion CHECK (
                jsonb_typeof(original_suggestion) = 'object'
            ),
            CONSTRAINT ck_recommendation_decisions_modified_suggestion CHECK (
                jsonb_typeof(modified_suggestion) = 'object'
            ),
            CONSTRAINT ck_recommendation_decisions_modified CHECK (
                (decision = 'APPROVED_WITH_CHANGES')
                = (modified_suggestion IS NOT NULL)
            )
        );
        CREATE INDEX ix_recommendation_decisions_rec
            ON ai.recommendation_decisions (recommendation_id, decided_at);
        COMMENT ON TABLE ai.recommendation_decisions IS
            'Append-only transitions of a recommendation: who, when and reason';

        CREATE TABLE ai.clinical_summaries (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            period_start timestamptz NOT NULL,
            period_end timestamptz NOT NULL,
            sections jsonb NOT NULL,
            numeric_check_matched boolean,
            requested_by uuid NOT NULL,
            {output_columns()},
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_clinical_summaries PRIMARY KEY (id),
            {clinic_patient_fks("clinical_summaries")},
            CONSTRAINT fk_clinical_summaries_requested_by FOREIGN KEY (requested_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinical_summaries_period CHECK (
                period_end > period_start
            ),
            CONSTRAINT ck_clinical_summaries_sections CHECK (
                jsonb_typeof(sections) = 'object'
            ),
            {output_checks("clinical_summaries")}
        );
        CREATE INDEX ix_clinical_summaries_patient
            ON ai.clinical_summaries (patient_id, generated_at DESC);
        COMMENT ON TABLE ai.clinical_summaries IS
            'IA5 pre-visit summaries with references to their data';

        CREATE TABLE ai.assistant_messages (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            conversation_id uuid NOT NULL,
            sender varchar(9) NOT NULL,
            content varchar(1000) NOT NULL,
            escalation_level varchar(14),
            guardrails_triggered varchar(40)[] NOT NULL DEFAULT '{{}}',
            context_doc_ids uuid[] NOT NULL DEFAULT '{{}}',
            external_consent_id uuid,
            model_name varchar(80),
            model_version varchar(80),
            input_hash char(64),
            explanation jsonb,
            is_fallback boolean NOT NULL DEFAULT false,
            fallback_reason varchar(17),
            sent_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_assistant_messages PRIMARY KEY (id),
            {clinic_patient_fks("assistant_messages")},
            CONSTRAINT fk_assistant_messages_external_consent_id
                FOREIGN KEY (external_consent_id)
                REFERENCES clinic.patient_consents (id) ON DELETE RESTRICT,
            CONSTRAINT ck_assistant_messages_sender CHECK (
                sender IN ('PATIENT', 'ASSISTANT')
            ),
            CONSTRAINT ck_assistant_messages_content CHECK (
                char_length(content) > 0
            ),
            CONSTRAINT ck_assistant_messages_escalation_level CHECK (
                escalation_level IN ('NONE', 'CONTACT_DOCTOR', 'EMERGENCY')
            ),
            CONSTRAINT ck_assistant_messages_explanation CHECK (
                jsonb_typeof(explanation) = 'object'
            ),
            CONSTRAINT ck_assistant_messages_fallback_reason CHECK (
                fallback_reason IN ({FALLBACK_REASONS})
            ),
            CONSTRAINT ck_assistant_messages_patient_message CHECK (
                sender = 'ASSISTANT'
                OR (
                    model_name IS NULL AND model_version IS NULL
                    AND input_hash IS NULL AND explanation IS NULL
                    AND escalation_level IS NULL
                )
            )
        );
        CREATE INDEX ix_assistant_messages_conversation
            ON ai.assistant_messages (patient_id, conversation_id, sent_at);
        COMMENT ON TABLE ai.assistant_messages IS
            'Educational assistant conversation (IA6); replies never give doses';
        """
    )

    # pg_trgm for the keyword search of the knowledge documents. The migrator
    # has no CREATE on public, so a missing extension goes to its own schema, and
    # the operator class is found wherever the extension lives.
    op.execute(
        """
        DO $trgm$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
                CREATE SCHEMA IF NOT EXISTS extensions;
                GRANT USAGE ON SCHEMA extensions
                    TO insumap_app, insumap_readonly, insumap_definer;
                CREATE EXTENSION pg_trgm SCHEMA extensions;
            END IF;
        END
        $trgm$;
        """
    )
    op.execute(
        """
        CREATE TABLE ai.knowledge_documents (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            title varchar(160) NOT NULL,
            body text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            status varchar(8) NOT NULL DEFAULT 'DRAFT',
            approved_by uuid,
            approved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_documents PRIMARY KEY (id),
            CONSTRAINT fk_knowledge_documents_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_knowledge_documents_approved_by FOREIGN KEY (approved_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_knowledge_documents_version CHECK (version > 0),
            CONSTRAINT ck_knowledge_documents_status CHECK (
                status IN ('DRAFT', 'APPROVED', 'RETIRED')
            )
        );
        CREATE INDEX ix_knowledge_documents_clinic_status
            ON ai.knowledge_documents (clinic_id, status);
        COMMENT ON TABLE ai.knowledge_documents IS
            'Educational material approved by the clinic; only APPROVED is used';

        DO $index$
        DECLARE
            trgm_schema text;
        BEGIN
            SELECT n.nspname INTO trgm_schema
              FROM pg_extension AS e JOIN pg_namespace AS n ON n.oid = e.extnamespace
             WHERE e.extname = 'pg_trgm';
            EXECUTE format(
                'CREATE INDEX ix_knowledge_documents_trgm ON ai.knowledge_documents'
                ' USING gin (title %1$I.gin_trgm_ops, body %1$I.gin_trgm_ops)'
                ' WHERE status = ''APPROVED''',
                trgm_schema
            );
        END
        $index$;

        CREATE TABLE ai.ai_evaluations (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            use_case varchar(3) NOT NULL,
            model_name varchar(80) NOT NULL,
            model_version varchar(80) NOT NULL,
            baseline_name varchar(80) NOT NULL,
            cohort_ref varchar(80) NOT NULL,
            sample_size integer NOT NULL,
            model_metrics jsonb NOT NULL,
            baseline_metrics jsonb NOT NULL,
            fallback_usage_pct numeric(5,2),
            evaluated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_ai_evaluations PRIMARY KEY (id),
            CONSTRAINT fk_ai_evaluations_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT ck_ai_evaluations_use_case CHECK (
                use_case IN ('IA1', 'IA2', 'IA3', 'IA4', 'IA5', 'IA6', 'IA7')
            ),
            CONSTRAINT ck_ai_evaluations_sample_size CHECK (sample_size > 0),
            CONSTRAINT ck_ai_evaluations_model_metrics CHECK (
                jsonb_typeof(model_metrics) = 'object'
            ),
            CONSTRAINT ck_ai_evaluations_baseline_metrics CHECK (
                jsonb_typeof(baseline_metrics) = 'object'
            ),
            CONSTRAINT ck_ai_evaluations_fallback_usage_pct CHECK (
                fallback_usage_pct BETWEEN 0 AND 100
            )
        );
        CREATE INDEX ix_ai_evaluations_use_case
            ON ai.ai_evaluations (use_case, evaluated_at DESC);
        COMMENT ON TABLE ai.ai_evaluations IS
            'Each AI use case against its fallback on simulated cohorts';
        """
    )

    # Close the circular references now that the ai tables exist.
    op.execute(
        """
        ALTER TABLE care.treatment_plans
            ADD CONSTRAINT fk_treatment_plans_source_recommendation_id
                FOREIGN KEY (source_recommendation_id)
                REFERENCES ai.recommendations (id) ON DELETE RESTRICT;
        ALTER TABLE care.follow_ups
            ADD CONSTRAINT fk_follow_ups_risk_assessment_id
                FOREIGN KEY (risk_assessment_id)
                REFERENCES ai.risk_assessments (id) ON DELETE RESTRICT;
        ALTER TABLE care.triage_reviews
            ADD CONSTRAINT fk_triage_reviews_risk_assessment_id
                FOREIGN KEY (risk_assessment_id)
                REFERENCES ai.risk_assessments (id) ON DELETE RESTRICT;
        ALTER TABLE alerts.alerts
            ADD CONSTRAINT fk_alerts_forecast_id FOREIGN KEY (forecast_id)
                REFERENCES ai.glucose_forecasts (id) ON DELETE RESTRICT;
        """
    )

    op.execute(
        "\n".join(
            f"CREATE TRIGGER tg_{table}_append_only\n"
            f"    BEFORE UPDATE OR DELETE ON ai.{table}\n"
            f"    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();"
            for table in APPEND_ONLY
        )
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE ai.{table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE ai.{table} FORCE ROW LEVEL SECURITY;"
            for table in RLS_TABLES
        )
    )

    op.execute(
        f"""
        CREATE POLICY glucose_forecasts_select ON ai.glucose_forecasts
            FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY glucose_forecasts_insert_system ON ai.glucose_forecasts
            FOR INSERT WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');

        CREATE POLICY risk_assessments_select ON ai.risk_assessments
            FOR SELECT USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY risk_assessments_insert_system ON ai.risk_assessments
            FOR INSERT WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');

        CREATE POLICY pattern_findings_select ON ai.pattern_findings
            FOR SELECT USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY pattern_findings_insert_system ON ai.pattern_findings
            FOR INSERT WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');

        CREATE POLICY recommendations_select ON ai.recommendations
            FOR SELECT USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY recommendations_insert_system ON ai.recommendations
            FOR INSERT WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');
        CREATE POLICY recommendations_update_staff ON ai.recommendations
            FOR UPDATE USING ({IN_CLINIC} AND {CLINICAL_STAFF})
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY recommendation_decisions_select
            ON ai.recommendation_decisions FOR SELECT
            USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY recommendation_decisions_insert
            ON ai.recommendation_decisions FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY clinical_summaries_select ON ai.clinical_summaries
            FOR SELECT USING ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY clinical_summaries_insert ON ai.clinical_summaries
            FOR INSERT WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});

        CREATE POLICY assistant_messages_select ON ai.assistant_messages
            FOR SELECT
            USING (
                {IN_CLINIC}
                AND (iam.app_role() = 'SYSTEM' OR ({OWN_PATIENT}))
            );
        CREATE POLICY assistant_messages_insert_own ON ai.assistant_messages
            FOR INSERT
            WITH CHECK ({IN_CLINIC} AND {OWN_PATIENT} AND sender = 'PATIENT');
        CREATE POLICY assistant_messages_insert_system ON ai.assistant_messages
            FOR INSERT
            WITH CHECK (
                {IN_CLINIC} AND iam.app_role() = 'SYSTEM' AND sender = 'ASSISTANT'
            );

        CREATE POLICY knowledge_documents_select ON ai.knowledge_documents
            FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR')
                    OR (
                        iam.app_role() IN ('PATIENT', 'SYSTEM')
                        AND status = 'APPROVED'
                    )
                )
            );
        CREATE POLICY knowledge_documents_insert_clinic_admin
            ON ai.knowledge_documents FOR INSERT WITH CHECK ({ADMIN_ONLY});
        CREATE POLICY knowledge_documents_update_clinic_admin
            ON ai.knowledge_documents FOR UPDATE
            USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY});

        CREATE POLICY ai_evaluations_select ON ai.ai_evaluations FOR SELECT
            USING (
                iam.app_clinic_id() IS NOT NULL
                AND iam.app_role() = 'CLINIC_ADMIN'
                AND (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())
            );
        CREATE POLICY ai_evaluations_insert_system ON ai.ai_evaluations
            FOR INSERT
            WITH CHECK (
                iam.app_role() = 'SYSTEM'
                AND (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())
            );
        CREATE POLICY ai_evaluations_migrator ON ai.ai_evaluations
            FOR ALL TO insumap_migrator USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT ON ai.glucose_forecasts, ai.risk_assessments,
            ai.pattern_findings, ai.recommendation_decisions,
            ai.clinical_summaries, ai.assistant_messages, ai.ai_evaluations
            TO insumap_app;
        GRANT SELECT, INSERT, UPDATE ON ai.recommendations,
            ai.knowledge_documents
            TO insumap_app;
        GRANT SELECT, INSERT ON ai.model_versions TO insumap_app;
        GRANT UPDATE (status, activated_at) ON ai.model_versions TO insumap_app;

        GRANT SELECT ON ai.model_versions, ai.glucose_forecasts,
            ai.risk_assessments, ai.pattern_findings, ai.recommendations,
            ai.recommendation_decisions, ai.clinical_summaries,
            ai.assistant_messages, ai.knowledge_documents, ai.ai_evaluations
            TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the circular foreign keys, the ai schema and the extension we added."""
    op.execute(
        """
        ALTER TABLE alerts.alerts DROP CONSTRAINT fk_alerts_forecast_id;
        ALTER TABLE care.triage_reviews
            DROP CONSTRAINT fk_triage_reviews_risk_assessment_id;
        ALTER TABLE care.follow_ups DROP CONSTRAINT fk_follow_ups_risk_assessment_id;
        ALTER TABLE care.treatment_plans
            DROP CONSTRAINT fk_treatment_plans_source_recommendation_id;

        DROP TABLE ai.ai_evaluations;
        DROP TABLE ai.knowledge_documents;
        DROP TABLE ai.assistant_messages;
        DROP TABLE ai.clinical_summaries;
        DROP TABLE ai.recommendation_decisions;
        DROP TABLE ai.recommendations;
        DROP TABLE ai.pattern_findings;
        DROP TABLE ai.risk_assessments;
        DROP TABLE ai.glucose_forecasts;
        DROP TABLE ai.model_versions;
        REVOKE USAGE ON SCHEMA ai
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP SCHEMA ai;

        DO $trgm$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension AS e
                  JOIN pg_namespace AS n ON n.oid = e.extnamespace
                 WHERE e.extname = 'pg_trgm' AND n.nspname = 'extensions'
            ) THEN
                DROP EXTENSION pg_trgm;
                REVOKE USAGE ON SCHEMA extensions
                    FROM insumap_app, insumap_readonly, insumap_definer;
                DROP SCHEMA extensions;
            END IF;
        END
        $trgm$;
        """
    )
