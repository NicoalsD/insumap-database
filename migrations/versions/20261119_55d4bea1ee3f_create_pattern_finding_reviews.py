"""Create ai.pattern_finding_reviews: the doctor dismisses a pattern finding.

RF-68, POST /patients/{patient_id}/pattern-findings/{finding_id}/dismiss. The
findings of IA3 are append-only, so the doctor's answer cannot be a column on
them: it is a row of its own. action holds the only decision of today,
DISMISSED, and leaves room for more. A finding is dismissed once
(UNIQUE (finding_id)), and the API answers 409 INVALID_STATE_TRANSITION when it
already is.

The foreign key to ai.pattern_findings is composite on (id, clinic_id,
patient_id), so a review can never name a clinic or a patient other than the
ones of its finding. That needs a unique constraint on the finding table, which
is only DDL: the table stays append-only.

Row level security copies ai.recommendation_decisions: the staff of the session
clinic reads; the insert is narrower, only a DOCTOR and only as itself
(reviewed_by is the session user). That the doctor is assigned to the patient is
checked by PatientRecordAccessProxy, as everywhere. There is no UPDATE or
DELETE policy or grant, and a trigger rejects both (audit.reject_mutation()).

Revision ID: 55d4bea1ee3f
Revises: 54ab7d646e8e
Create Date: 2026-11-19 18:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "55d4bea1ee3f"
down_revision: str | None = "54ab7d646e8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table, the trigger, the policies and the grants."""
    op.execute(
        """
        ALTER TABLE ai.pattern_findings
            ADD CONSTRAINT uq_pattern_findings_id_clinic_patient
                UNIQUE (id, clinic_id, patient_id);

        CREATE TABLE ai.pattern_finding_reviews (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            action varchar(9) NOT NULL,
            reason varchar(500),
            reviewed_by uuid NOT NULL,
            reviewed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_pattern_finding_reviews PRIMARY KEY (id),
            CONSTRAINT fk_pattern_finding_reviews_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_pattern_finding_reviews_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_pattern_finding_reviews_finding_id
                FOREIGN KEY (finding_id, clinic_id, patient_id)
                REFERENCES ai.pattern_findings (id, clinic_id, patient_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_pattern_finding_reviews_reviewed_by
                FOREIGN KEY (reviewed_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT uq_pattern_finding_reviews_finding_id UNIQUE (finding_id),
            CONSTRAINT ck_pattern_finding_reviews_action CHECK (
                action IN ('DISMISSED')
            ),
            CONSTRAINT ck_pattern_finding_reviews_reason CHECK (
                char_length(btrim(reason)) BETWEEN 1 AND 500
            )
        );
        CREATE INDEX ix_pattern_finding_reviews_patient
            ON ai.pattern_finding_reviews (patient_id, reviewed_at DESC);
        COMMENT ON TABLE ai.pattern_finding_reviews IS
            'Append-only answer of the doctor to a pattern finding: who, when, why';

        CREATE TRIGGER tg_pattern_finding_reviews_append_only
            BEFORE UPDATE OR DELETE ON ai.pattern_finding_reviews
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

        ALTER TABLE ai.pattern_finding_reviews ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ai.pattern_finding_reviews FORCE ROW LEVEL SECURITY;
        CREATE POLICY pattern_finding_reviews_select
            ON ai.pattern_finding_reviews FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() IN ('DOCTOR', 'SYSTEM')
            );
        CREATE POLICY pattern_finding_reviews_insert
            ON ai.pattern_finding_reviews FOR INSERT
            WITH CHECK (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() = 'DOCTOR'
                AND reviewed_by = iam.app_user_id()
            );

        GRANT SELECT, INSERT ON ai.pattern_finding_reviews TO insumap_app;
        GRANT SELECT ON ai.pattern_finding_reviews TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the table and the unique constraint it needed."""
    op.execute(
        """
        DROP TABLE ai.pattern_finding_reviews;
        ALTER TABLE ai.pattern_findings
            DROP CONSTRAINT uq_pattern_findings_id_clinic_patient;
        """
    )
