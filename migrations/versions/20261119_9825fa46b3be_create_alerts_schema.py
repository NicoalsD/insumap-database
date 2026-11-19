"""Create the alerts schema: rules, alerts, history and notifications.

Modelo de datos 9.5, Diccionario section 8. Seven tables: alert_rules, alerts,
alert_status_history, notifications, notification_deliveries,
push_subscriptions and notification_preferences.

alerts.alerts.forecast_id references ai.glucose_forecasts, which does not exist
yet: the ai revision adds that foreign key.

Triggers:

- care.reject_active_version_update() (created by the care revision) makes an
  ACTIVE alert rule immutable except for its change to RETIRED.
- audit.reject_mutation() makes alert_status_history append-only.

Row level security (Modelo de datos 4.3 to 4.5):

- alert_rules (RLS-3): CLINIC_ADMIN, DOCTOR and SYSTEM read global rows and the
  session clinic; CLINIC_ADMIN writes its own clinic; insumap_migrator seeds.
- alerts (RLS-2 with role exclusion): DOCTOR and SYSTEM work on the session
  clinic, only SYSTEM creates alerts, a PATIENT reads its own and may only
  acknowledge them (OPEN or ESCALATED to ACKNOWLEDGED by itself). No branch for
  CLINIC_ADMIN or PLATFORM_ADMIN: the administrator sees counts through a
  SECURITY DEFINER function (a later slice).
- alert_status_history follows the visibility of the alert; the patient inserts
  the row of its own acknowledgement.
- notifications, push_subscriptions, notification_preferences (RLS-6): the row
  belongs to the user (recipient_user_id or user_id) and its clinic is the one
  of the session (NULL for PLATFORM_ADMIN). SYSTEM inserts notifications and
  reads the subscriptions and the preferences of its clinic.
- notification_deliveries (RLS-1 with role): SYSTEM writes; a recipient reads
  the deliveries of its own notifications.

push_subscriptions.auth_ciphertext is a class S column: insumap_readonly has no
access to that table. insumap_app can DELETE only push_subscriptions (and
password_history in iam).

Revision ID: 9825fa46b3be
Revises: 6501d51b9a95
Create Date: 2026-11-19 10:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9825fa46b3be"
down_revision: str | None = "6501d51b9a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "alert_rules",
    "alerts",
    "alert_status_history",
    "notifications",
    "notification_deliveries",
    "push_subscriptions",
    "notification_preferences",
)

IN_CLINIC = "clinic_id = iam.app_clinic_id()"
OWN_PATIENT = "iam.app_role() = 'PATIENT' AND patient_id = clinic.current_patient_id()"
CLINICAL_STAFF = "iam.app_role() IN ('DOCTOR', 'SYSTEM')"
ADMIN_ONLY = f"{IN_CLINIC} AND iam.app_role() = 'CLINIC_ADMIN'"
# RLS-6 with tenant: the row is the session user's, in the session clinic.
OWN_USER = (
    "user_id = iam.app_user_id() AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()"
)
ALERT_TYPES = (
    "'HYPOGLYCEMIA', 'HYPERGLYCEMIA', 'RATE_OF_CHANGE', 'ADHERENCE', 'PREDICTIVE',"
    " 'SYMPTOM_ALARM'"
)
ALERT_STATUSES = "'OPEN', 'ACKNOWLEDGED', 'ESCALATED', 'RESOLVED'"


def upgrade() -> None:
    """Create the schema, tables, triggers, policies and grants."""
    op.execute(
        """
        CREATE SCHEMA alerts;
        COMMENT ON SCHEMA alerts IS 'Alerts and notifications';
        GRANT USAGE ON SCHEMA alerts
            TO insumap_app, insumap_readonly, insumap_definer;
        """
    )

    op.execute(
        f"""
        CREATE TABLE alerts.alert_rules (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            code varchar(40) NOT NULL,
            name varchar(120) NOT NULL,
            alert_type varchar(14) NOT NULL,
            level smallint,
            priority varchar(8) NOT NULL,
            definition jsonb NOT NULL,
            escalation jsonb NOT NULL,
            bypass_quiet_hours boolean NOT NULL DEFAULT false,
            patient_notice varchar(300),
            version integer NOT NULL,
            status varchar(7) NOT NULL DEFAULT 'DRAFT',
            change_reason varchar(500),
            created_by uuid,
            activated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_alert_rules PRIMARY KEY (id),
            CONSTRAINT fk_alert_rules_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alert_rules_created_by FOREIGN KEY (created_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_alert_rules_code CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_alert_rules_alert_type CHECK (
                alert_type IN ({ALERT_TYPES})
            ),
            CONSTRAINT ck_alert_rules_level CHECK (level BETWEEN 1 AND 3),
            CONSTRAINT ck_alert_rules_priority CHECK (
                priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
            ),
            CONSTRAINT ck_alert_rules_definition CHECK (
                jsonb_typeof(definition) = 'object'
            ),
            CONSTRAINT ck_alert_rules_escalation CHECK (
                jsonb_typeof(escalation) = 'array'
            ),
            CONSTRAINT ck_alert_rules_version CHECK (version > 0),
            CONSTRAINT ck_alert_rules_status CHECK (
                status IN ('DRAFT', 'ACTIVE', 'RETIRED')
            ),
            CONSTRAINT ck_alert_rules_change_reason_length CHECK (
                char_length(change_reason) BETWEEN 10 AND 500
            )
        );
        CREATE UNIQUE INDEX ux_alert_rules_version
            ON alerts.alert_rules (clinic_id, code, version) NULLS NOT DISTINCT;
        CREATE UNIQUE INDEX ux_alert_rules_active
            ON alerts.alert_rules (clinic_id, code) NULLS NOT DISTINCT
            WHERE status = 'ACTIVE';
        CREATE INDEX ix_alert_rules_type_active
            ON alerts.alert_rules (alert_type) WHERE status = 'ACTIVE';
        CREATE TRIGGER tg_alert_rules_active_version
            BEFORE UPDATE ON alerts.alert_rules
            FOR EACH ROW EXECUTE FUNCTION care.reject_active_version_update();
        COMMENT ON TABLE alerts.alert_rules IS
            'Versioned alert rule trees (Composite, P08); thresholds are data';

        CREATE TABLE alerts.alerts (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid NOT NULL,
            rule_id uuid NOT NULL,
            alert_type varchar(14) NOT NULL,
            level smallint,
            priority varchar(8) NOT NULL,
            status varchar(12) NOT NULL DEFAULT 'OPEN',
            escalation_level smallint NOT NULL DEFAULT 0,
            next_escalation_at timestamptz,
            opened_at timestamptz NOT NULL DEFAULT now(),
            trigger_reading_id uuid,
            forecast_id uuid,
            evidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            is_fallback boolean NOT NULL DEFAULT false,
            acknowledged_by uuid,
            acknowledged_at timestamptz,
            resolved_by uuid,
            resolved_at timestamptz,
            resolution_note varchar(500),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_alerts PRIMARY KEY (id),
            CONSTRAINT fk_alerts_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alerts_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alerts_rule_id FOREIGN KEY (rule_id)
                REFERENCES alerts.alert_rules (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alerts_trigger_reading_id FOREIGN KEY (trigger_reading_id)
                REFERENCES monitoring.glucose_readings (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alerts_acknowledged_by FOREIGN KEY (acknowledged_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alerts_resolved_by FOREIGN KEY (resolved_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_alerts_alert_type CHECK (alert_type IN ({ALERT_TYPES})),
            CONSTRAINT ck_alerts_level CHECK (level BETWEEN 1 AND 3),
            CONSTRAINT ck_alerts_priority CHECK (
                priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
            ),
            CONSTRAINT ck_alerts_status CHECK (status IN ({ALERT_STATUSES})),
            CONSTRAINT ck_alerts_escalation_level CHECK (escalation_level >= 0),
            CONSTRAINT ck_alerts_evidence CHECK (jsonb_typeof(evidence) = 'object'),
            CONSTRAINT ck_alerts_resolution_note_length CHECK (
                char_length(resolution_note) BETWEEN 10 AND 500
            ),
            CONSTRAINT ck_alerts_lock_version CHECK (lock_version >= 0)
        );
        CREATE UNIQUE INDEX ux_alerts_open_per_rule
            ON alerts.alerts (patient_id, rule_id)
            WHERE status IN ('OPEN', 'ESCALATED');
        CREATE INDEX ix_alerts_patient_status
            ON alerts.alerts (patient_id, status, opened_at DESC);
        CREATE INDEX ix_alerts_escalation_due
            ON alerts.alerts (next_escalation_at)
            WHERE status IN ('OPEN', 'ESCALATED');
        CREATE INDEX ix_alerts_clinic_opened
            ON alerts.alerts (clinic_id, opened_at DESC);
        COMMENT ON TABLE alerts.alerts IS
            'Alerts raised by a rule, a forecast or an alarm symptom (state P18)';

        CREATE TABLE alerts.alert_status_history (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            alert_id uuid NOT NULL,
            from_status varchar(12),
            to_status varchar(12) NOT NULL,
            escalation_level smallint NOT NULL DEFAULT 0,
            actor_user_id uuid,
            actor_type varchar(6) NOT NULL,
            note varchar(500),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_alert_status_history PRIMARY KEY (id),
            CONSTRAINT fk_alert_status_history_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alert_status_history_alert_id FOREIGN KEY (alert_id)
                REFERENCES alerts.alerts (id) ON DELETE RESTRICT,
            CONSTRAINT fk_alert_status_history_actor_user_id
                FOREIGN KEY (actor_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_alert_status_history_from_status CHECK (
                from_status IN ({ALERT_STATUSES})
            ),
            CONSTRAINT ck_alert_status_history_to_status CHECK (
                to_status IN ({ALERT_STATUSES})
            ),
            CONSTRAINT ck_alert_status_history_escalation_level CHECK (
                escalation_level >= 0
            ),
            CONSTRAINT ck_alert_status_history_actor_type CHECK (
                actor_type IN ('USER', 'SYSTEM')
            ),
            CONSTRAINT ck_alert_status_history_note_length CHECK (
                char_length(note) BETWEEN 10 AND 500
            )
        );
        CREATE INDEX ix_alert_status_history_alert
            ON alerts.alert_status_history (alert_id, occurred_at);
        CREATE TRIGGER tg_alert_status_history_append_only
            BEFORE UPDATE OR DELETE ON alerts.alert_status_history
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
        COMMENT ON TABLE alerts.alert_status_history IS
            'Append-only transitions of each alert: who, when and note';
        """
    )

    op.execute(
        """
        CREATE TABLE alerts.notifications (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            recipient_user_id uuid NOT NULL,
            notification_type varchar(10) NOT NULL,
            message_key varchar(60) NOT NULL,
            entity_type varchar(20),
            entity_id uuid,
            alert_id uuid,
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            read_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_notifications PRIMARY KEY (id),
            CONSTRAINT fk_notifications_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_notifications_recipient_user_id
                FOREIGN KEY (recipient_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_notifications_alert_id FOREIGN KEY (alert_id)
                REFERENCES alerts.alerts (id) ON DELETE RESTRICT,
            CONSTRAINT ck_notifications_notification_type CHECK (
                notification_type IN (
                    'ALERT', 'REMINDER', 'ADHERENCE', 'FOLLOW_UP', 'MESSAGE',
                    'CONSENT'
                )
            ),
            CONSTRAINT ck_notifications_params CHECK (jsonb_typeof(params) = 'object')
        );
        CREATE INDEX ix_notifications_unread
            ON alerts.notifications (recipient_user_id, created_at DESC)
            WHERE read_at IS NULL;
        COMMENT ON TABLE alerts.notifications IS
            'In-app notifications: a text key and references, never clinical values';

        CREATE TABLE alerts.notification_deliveries (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            notification_id uuid NOT NULL,
            channel varchar(8) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'PENDING',
            attempts smallint NOT NULL DEFAULT 0,
            last_error_code varchar(40),
            sent_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_notification_deliveries PRIMARY KEY (id),
            CONSTRAINT fk_notification_deliveries_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_notification_deliveries_notification_id
                FOREIGN KEY (notification_id)
                REFERENCES alerts.notifications (id) ON DELETE RESTRICT,
            CONSTRAINT ck_notification_deliveries_channel CHECK (
                channel IN ('EMAIL', 'WEB_PUSH', 'IN_APP')
            ),
            CONSTRAINT ck_notification_deliveries_status CHECK (
                status IN (
                    'PENDING', 'SENT', 'FAILED', 'SKIPPED_QUIET_HOURS',
                    'SKIPPED_DISABLED'
                )
            ),
            CONSTRAINT ck_notification_deliveries_attempts CHECK (attempts >= 0)
        );
        CREATE UNIQUE INDEX ux_notification_deliveries_channel
            ON alerts.notification_deliveries (notification_id, channel);
        CREATE INDEX ix_notification_deliveries_pending
            ON alerts.notification_deliveries (created_at)
            WHERE status = 'PENDING';
        COMMENT ON TABLE alerts.notification_deliveries IS
            'Delivery of each notification by channel; a failed channel is logged';

        CREATE TABLE alerts.push_subscriptions (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            user_id uuid NOT NULL,
            endpoint varchar(2048) NOT NULL,
            endpoint_hash char(64) NOT NULL,
            p256dh varchar(255) NOT NULL,
            auth_ciphertext bytea NOT NULL,
            key_id char(16) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            CONSTRAINT pk_push_subscriptions PRIMARY KEY (id),
            CONSTRAINT fk_push_subscriptions_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_push_subscriptions_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_push_subscriptions_endpoint CHECK (endpoint ~ '^https://')
        );
        CREATE UNIQUE INDEX ux_push_subscriptions_endpoint_hash
            ON alerts.push_subscriptions (endpoint_hash);
        CREATE INDEX ix_push_subscriptions_user_active
            ON alerts.push_subscriptions (user_id) WHERE is_active;
        COMMENT ON TABLE alerts.push_subscriptions IS
            'Web Push subscriptions; the endpoint is checked against allowed providers';
        COMMENT ON COLUMN alerts.push_subscriptions.auth_ciphertext IS
            'Push auth secret encrypted with AES-256-GCM. Secret (class S)';

        CREATE TABLE alerts.notification_preferences (
            user_id uuid NOT NULL,
            clinic_id uuid,
            email_enabled boolean NOT NULL DEFAULT true,
            web_push_enabled boolean NOT NULL DEFAULT true,
            in_app_enabled boolean NOT NULL DEFAULT true,
            quiet_start time,
            quiet_end time,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_notification_preferences PRIMARY KEY (user_id),
            CONSTRAINT fk_notification_preferences_user_id FOREIGN KEY (user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_notification_preferences_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT ck_notification_preferences_quiet CHECK (
                (quiet_start IS NULL) = (quiet_end IS NULL)
            )
        );
        COMMENT ON TABLE alerts.notification_preferences IS
            'Enabled channels and quiet hours; critical alerts ignore the silence';
        """
    )

    op.execute(
        "\n".join(
            f"ALTER TABLE alerts.{table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE alerts.{table} FORCE ROW LEVEL SECURITY;"
            for table in TABLES
        )
    )

    # alert_rules (RLS-3).
    op.execute(
        f"""
        CREATE POLICY alert_rules_select ON alerts.alert_rules FOR SELECT
            USING (
                iam.app_clinic_id() IS NOT NULL
                AND iam.app_role() IN ('CLINIC_ADMIN', 'DOCTOR', 'SYSTEM')
                AND (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())
            );
        CREATE POLICY alert_rules_insert_clinic_admin ON alerts.alert_rules
            FOR INSERT WITH CHECK ({ADMIN_ONLY});
        CREATE POLICY alert_rules_update_clinic_admin ON alerts.alert_rules
            FOR UPDATE USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY});
        CREATE POLICY alert_rules_migrator ON alerts.alert_rules
            FOR ALL TO insumap_migrator USING (true) WITH CHECK (true);
        """
    )

    # alerts and alert_status_history (RLS-2 with role exclusion).
    op.execute(
        f"""
        CREATE POLICY alerts_select ON alerts.alerts FOR SELECT
            USING ({IN_CLINIC} AND ({CLINICAL_STAFF} OR ({OWN_PATIENT})));
        CREATE POLICY alerts_insert_system ON alerts.alerts FOR INSERT
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');
        CREATE POLICY alerts_update_staff ON alerts.alerts FOR UPDATE
            USING ({IN_CLINIC} AND {CLINICAL_STAFF})
            WITH CHECK ({IN_CLINIC} AND {CLINICAL_STAFF});
        CREATE POLICY alerts_update_own ON alerts.alerts FOR UPDATE
            USING (
                {IN_CLINIC} AND {OWN_PATIENT}
                AND status IN ('OPEN', 'ESCALATED')
            )
            WITH CHECK (
                {IN_CLINIC} AND {OWN_PATIENT}
                AND status = 'ACKNOWLEDGED'
                AND acknowledged_by = iam.app_user_id()
            );

        CREATE POLICY alert_status_history_select
            ON alerts.alert_status_history FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND alert_id IN (SELECT a.id FROM alerts.alerts AS a)
                    )
                )
            );
        CREATE POLICY alert_status_history_insert
            ON alerts.alert_status_history FOR INSERT
            WITH CHECK (
                {IN_CLINIC}
                AND (
                    {CLINICAL_STAFF}
                    OR (
                        iam.app_role() = 'PATIENT'
                        AND actor_type = 'USER'
                        AND actor_user_id = iam.app_user_id()
                        AND alert_id IN (SELECT a.id FROM alerts.alerts AS a)
                    )
                )
            );
        """
    )

    # notifications, deliveries, subscriptions and preferences.
    op.execute(
        f"""
        CREATE POLICY notifications_select_own ON alerts.notifications
            FOR SELECT
            USING (
                recipient_user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            );
        CREATE POLICY notifications_insert_system ON alerts.notifications
            FOR INSERT
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');
        CREATE POLICY notifications_update_own ON alerts.notifications
            FOR UPDATE
            USING (
                recipient_user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            )
            WITH CHECK (
                recipient_user_id = iam.app_user_id()
                AND clinic_id IS NOT DISTINCT FROM iam.app_clinic_id()
            );

        CREATE POLICY notification_deliveries_select
            ON alerts.notification_deliveries FOR SELECT
            USING (
                {IN_CLINIC}
                AND (
                    iam.app_role() = 'SYSTEM'
                    OR notification_id IN (
                        SELECT n.id FROM alerts.notifications AS n
                    )
                )
            );
        CREATE POLICY notification_deliveries_insert_system
            ON alerts.notification_deliveries FOR INSERT
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');
        CREATE POLICY notification_deliveries_update_system
            ON alerts.notification_deliveries FOR UPDATE
            USING ({IN_CLINIC} AND iam.app_role() = 'SYSTEM')
            WITH CHECK ({IN_CLINIC} AND iam.app_role() = 'SYSTEM');

        CREATE POLICY push_subscriptions_select ON alerts.push_subscriptions
            FOR SELECT
            USING (
                ({OWN_USER})
                OR ({IN_CLINIC} AND iam.app_role() = 'SYSTEM')
            );
        CREATE POLICY push_subscriptions_insert_own ON alerts.push_subscriptions
            FOR INSERT WITH CHECK ({OWN_USER});
        CREATE POLICY push_subscriptions_update ON alerts.push_subscriptions
            FOR UPDATE
            USING (
                ({OWN_USER})
                OR ({IN_CLINIC} AND iam.app_role() = 'SYSTEM')
            )
            WITH CHECK (
                ({OWN_USER})
                OR ({IN_CLINIC} AND iam.app_role() = 'SYSTEM')
            );
        CREATE POLICY push_subscriptions_delete_own ON alerts.push_subscriptions
            FOR DELETE USING ({OWN_USER});

        CREATE POLICY notification_preferences_select
            ON alerts.notification_preferences FOR SELECT
            USING (
                ({OWN_USER})
                OR ({IN_CLINIC} AND iam.app_role() = 'SYSTEM')
            );
        CREATE POLICY notification_preferences_insert_own
            ON alerts.notification_preferences FOR INSERT
            WITH CHECK ({OWN_USER});
        CREATE POLICY notification_preferences_update_own
            ON alerts.notification_preferences FOR UPDATE
            USING ({OWN_USER}) WITH CHECK ({OWN_USER});
        """
    )

    # insumap_app: minimum DML. The recipient only marks a notification read, and
    # a subscription changes only its keys and state.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON alerts.alert_rules, alerts.alerts,
            alerts.notification_deliveries, alerts.notification_preferences
            TO insumap_app;
        GRANT SELECT, INSERT ON alerts.alert_status_history, alerts.notifications
            TO insumap_app;
        GRANT UPDATE (read_at) ON alerts.notifications TO insumap_app;
        GRANT SELECT, INSERT, DELETE ON alerts.push_subscriptions TO insumap_app;
        GRANT UPDATE (p256dh, auth_ciphertext, key_id, is_active, last_used_at)
            ON alerts.push_subscriptions TO insumap_app;
        """
    )

    # insumap_readonly: everything except the push subscriptions (class S).
    op.execute(
        """
        GRANT SELECT ON alerts.alert_rules, alerts.alerts,
            alerts.alert_status_history, alerts.notifications,
            alerts.notification_deliveries, alerts.notification_preferences
            TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Drop the alerts schema (its policies, triggers and privileges go with it)."""
    op.execute(
        """
        DROP TABLE alerts.notification_preferences;
        DROP TABLE alerts.push_subscriptions;
        DROP TABLE alerts.notification_deliveries;
        DROP TABLE alerts.notifications;
        DROP TABLE alerts.alert_status_history;
        DROP TABLE alerts.alerts;
        DROP TABLE alerts.alert_rules;
        REVOKE USAGE ON SCHEMA alerts
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP SCHEMA alerts;
        """
    )
