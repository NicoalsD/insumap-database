"""Create the messaging schema: threads, messages and read marks.

Modelo de datos 9.7, Diccionario section 10. Three tables: threads (one per
patient), messages (append-only) and thread_reads (the last read time of each
participant, so the unread counter does not need to update messages).

Row level security (RLS-2 with role exclusion, Modelo de datos 4.5): a PATIENT
reads and writes inside its own thread, DOCTOR writes and reads the clinic and
SYSTEM only reads. CLINIC_ADMIN and PLATFORM_ADMIN have no access (V8). The
doctor to patient assignment is checked by PatientRecordAccessProxy. A sender
must be the session user and its sender_role must match the session role.
thread_reads is RLS-6 with tenant: each participant owns its own mark.

Revision ID: e80880113a84
Revises: 3ac48ed3d0e8
Create Date: 2026-11-19 11:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e80880113a84"
down_revision: str | None = "3ac48ed3d0e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("threads", "messages", "thread_reads")

IN_CLINIC = "clinic_id = iam.app_clinic_id()"
OWN_PATIENT = "iam.app_role() = 'PATIENT' AND patient_id = clinic.current_patient_id()"
CLINICAL_STAFF = "iam.app_role() IN ('DOCTOR', 'SYSTEM')"
# A thread the session can already see (the RLS of threads applies inside).
OWN_THREAD = "thread_id IN (SELECT t.id FROM messaging.threads AS t)"


def upgrade() -> None:
    """Create the schema, tables, trigger, policies and grants."""
    op.execute(
        """
        CREATE SCHEMA messaging;
        COMMENT ON SCHEMA messaging IS 'Messages between patient and care team';
        GRANT USAGE ON SCHEMA messaging
            TO insumap_app, insumap_readonly, insumap_definer;

        CREATE TABLE messaging.threads (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            last_message_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_threads PRIMARY KEY (id),
            CONSTRAINT fk_threads_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_threads_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_threads_patient_id ON messaging.threads (patient_id);
        CREATE INDEX ix_threads_clinic_last_message
            ON messaging.threads (clinic_id, last_message_at DESC);
        COMMENT ON TABLE messaging.threads IS
            'One thread per patient with its care team (Post-MVP, RF-84)';

        CREATE TABLE messaging.messages (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            thread_id uuid NOT NULL,
            sender_user_id uuid NOT NULL,
            sender_role varchar(7) NOT NULL,
            body varchar(1000) NOT NULL,
            sent_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_messages PRIMARY KEY (id),
            CONSTRAINT fk_messages_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_messages_thread_id FOREIGN KEY (thread_id)
                REFERENCES messaging.threads (id) ON DELETE RESTRICT,
            CONSTRAINT fk_messages_sender_user_id FOREIGN KEY (sender_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_messages_sender_role CHECK (
                sender_role IN ('PATIENT', 'DOCTOR')
            ),
            CONSTRAINT ck_messages_body CHECK (char_length(body) > 0)
        );
        CREATE INDEX ix_messages_thread_sent
            ON messaging.messages (thread_id, sent_at DESC);
        CREATE TRIGGER tg_messages_append_only
            BEFORE UPDATE OR DELETE ON messaging.messages
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
        COMMENT ON TABLE messaging.messages IS
            'Append-only messages; not an emergency channel (the app says so)';

        CREATE TABLE messaging.thread_reads (
            thread_id uuid NOT NULL,
            user_id uuid NOT NULL,
            clinic_id uuid NOT NULL,
            last_read_at timestamptz NOT NULL,
            CONSTRAINT pk_thread_reads PRIMARY KEY (thread_id, user_id),
            CONSTRAINT fk_thread_reads_thread_id FOREIGN KEY (thread_id)
                REFERENCES messaging.threads (id) ON DELETE RESTRICT,
            CONSTRAINT fk_thread_reads_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_thread_reads_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT
        );
        COMMENT ON TABLE messaging.thread_reads IS
            'Last read time of each participant (unread counter)';
        """
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE messaging.{table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE messaging.{table} FORCE ROW LEVEL SECURITY;"
            for table in TABLES
        )
    )

    op.execute(
        f"""
        CREATE POLICY threads_select ON messaging.threads FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY threads_insert ON messaging.threads FOR INSERT
            WITH CHECK (
                {IN_CLINIC} AND (iam.app_role() = 'DOCTOR' OR ({OWN_PATIENT}))
            );
        CREATE POLICY threads_update ON messaging.threads FOR UPDATE
            USING ({IN_CLINIC} AND (iam.app_role() = 'DOCTOR' OR ({OWN_PATIENT})))
            WITH CHECK (
                {IN_CLINIC} AND (iam.app_role() = 'DOCTOR' OR ({OWN_PATIENT}))
            );

        CREATE POLICY messages_select ON messaging.messages FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (iam.app_role() = 'PATIENT' AND {OWN_THREAD})
                )
            );
        CREATE POLICY messages_insert ON messaging.messages FOR INSERT
            WITH CHECK (
                {IN_CLINIC}
                AND sender_user_id = iam.app_user_id()
                AND (
                    (
                        iam.app_role() = 'PATIENT' AND sender_role = 'PATIENT'
                        AND {OWN_THREAD}
                    )
                    OR (iam.app_role() = 'DOCTOR' AND sender_role = 'DOCTOR')
                )
            );

        CREATE POLICY thread_reads_select_own ON messaging.thread_reads
            FOR SELECT
            USING (
                {IN_CLINIC} AND user_id = iam.app_user_id()
                AND iam.app_role() IN ('PATIENT', 'DOCTOR')
            );
        CREATE POLICY thread_reads_insert_own ON messaging.thread_reads
            FOR INSERT
            WITH CHECK (
                {IN_CLINIC} AND user_id = iam.app_user_id()
                AND iam.app_role() IN ('PATIENT', 'DOCTOR')
                AND {OWN_THREAD}
            );
        CREATE POLICY thread_reads_update_own ON messaging.thread_reads
            FOR UPDATE
            USING (
                {IN_CLINIC} AND user_id = iam.app_user_id()
                AND iam.app_role() IN ('PATIENT', 'DOCTOR')
            )
            WITH CHECK (
                {IN_CLINIC} AND user_id = iam.app_user_id()
                AND iam.app_role() IN ('PATIENT', 'DOCTOR')
            );
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT ON messaging.threads, messaging.messages,
            messaging.thread_reads
            TO insumap_app;
        GRANT UPDATE (last_message_at) ON messaging.threads TO insumap_app;
        GRANT UPDATE (last_read_at) ON messaging.thread_reads TO insumap_app;
        GRANT SELECT ON messaging.threads, messaging.messages,
            messaging.thread_reads
            TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the messaging schema (its policies and privileges go with it)."""
    op.execute(
        """
        DROP TABLE messaging.thread_reads;
        DROP TABLE messaging.messages;
        DROP TABLE messaging.threads;
        REVOKE USAGE ON SCHEMA messaging
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP SCHEMA messaging;
        """
    )
