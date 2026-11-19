"""Create the audit tables: action log with hash chain, PHI access log, retention.

Modelo de datos 9.8, Diccionario section audit. Three tables:

- audit.audit_log: actions that change security data or clinical decisions,
  with a hash chain per clinic. insumap_app has no INSERT: it writes only through
  audit.append_audit_log(...) (SECURITY DEFINER, RLS-5). A BEFORE INSERT trigger
  takes an advisory lock per clinic, assigns chain_seq and prev_hash and computes
  row_hash = sha256(prev_hash || canonical content). audit.verify_chain(clinic)
  recomputes it and returns the first broken row.
- audit.data_access_log: every read of clinical data (PhiAccessAuditDecorator,
  P09). Insert open to the session clinic; read by CLINIC_ADMIN (RLS-1).
- audit.retention_policies: retention period per table as data (RLS-3). Seeds
  are written by the migrator; changes go through audit.set_retention_policy.

All three are append-only (audit.reject_mutation) except retention_policies,
which is updated only by its definer function. No PLATFORM_ADMIN branch (V7).

Canonical content of the hash (proposal fixed here): the columns from clinic_id
to ip_hmac, in table order, as text, joined with the unit separator 0x1F;
timestamps in ISO-8601 UTC with microseconds and NULL as the empty string.

Revision ID: b7d41c2e9a10
Revises: e80880113a84
Create Date: 2026-11-19 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d41c2e9a10"
down_revision: str | None = "e80880113a84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR_ROLES = "'PLATFORM_ADMIN', 'CLINIC_ADMIN', 'DOCTOR', 'PATIENT', 'SYSTEM'"
RESOURCE_TYPES = (
    "'GLUCOSE_READINGS', 'INSULIN_DOSES', 'MEALS', 'PHYSICAL_ACTIVITIES',"
    " 'SYMPTOMS', 'LAB_RESULTS', 'GLYCEMIC_METRICS', 'TREATMENT_PLAN',"
    " 'CLINICAL_NOTES', 'ALERTS', 'AI_OUTPUTS', 'MESSAGES', 'PATIENT_TIMELINE',"
    " 'REPORT', 'TRIAGE_LIST', 'PATIENT_DEMOGRAPHICS', 'PATIENT_LIST',"
    " 'PATIENT_DOCUMENT'"
)
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500
GENESIS_HASH = "0" * 64


def upgrade() -> None:
    """Create the tables, the chain trigger, the definer functions and the RLS."""
    op.execute(
        f"""
        CREATE TABLE audit.audit_log (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            chain_seq bigint NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            actor_user_id uuid,
            actor_role varchar(14),
            actor_type varchar(6) NOT NULL,
            action varchar(60) NOT NULL,
            entity_type varchar(40) NOT NULL,
            entity_id uuid,
            entity_version integer,
            reason varchar(500),
            changed_fields varchar(80)[] NOT NULL DEFAULT '{{}}',
            request_id varchar(64),
            ip_hmac char(64),
            prev_hash char(64) NOT NULL,
            row_hash char(64) NOT NULL,
            CONSTRAINT pk_audit_log PRIMARY KEY (id),
            CONSTRAINT fk_audit_log_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_log_actor_user_id FOREIGN KEY (actor_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_audit_log_chain_seq CHECK (chain_seq > 0),
            CONSTRAINT ck_audit_log_actor_role CHECK (actor_role IN ({ACTOR_ROLES})),
            CONSTRAINT ck_audit_log_actor_type CHECK (actor_type IN ('USER', 'SYSTEM')),
            CONSTRAINT ck_audit_log_action CHECK (action ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_audit_log_reason_length CHECK (
                char_length(reason) BETWEEN {REASON_MIN_LENGTH} AND {REASON_MAX_LENGTH}
            )
        );
        CREATE UNIQUE INDEX ux_audit_log_clinic_seq
            ON audit.audit_log (clinic_id, chain_seq);
        CREATE INDEX ix_audit_log_clinic_occurred
            ON audit.audit_log (clinic_id, occurred_at DESC);
        CREATE INDEX ix_audit_log_actor
            ON audit.audit_log (clinic_id, actor_user_id, occurred_at DESC);
        CREATE INDEX ix_audit_log_entity
            ON audit.audit_log (clinic_id, entity_type, entity_id);
        COMMENT ON TABLE audit.audit_log IS
            'Actions on security data and clinical decisions, hash chained per clinic';

        CREATE TABLE audit.data_access_log (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid NOT NULL,
            patient_id uuid,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            actor_user_id uuid,
            actor_role varchar(14),
            actor_type varchar(6) NOT NULL,
            resource_type varchar(24) NOT NULL,
            action varchar(7) NOT NULL DEFAULT 'READ',
            outcome varchar(7) NOT NULL,
            purpose varchar(14) NOT NULL,
            request_id varchar(64),
            ip_hmac char(64),
            CONSTRAINT pk_data_access_log PRIMARY KEY (id),
            CONSTRAINT fk_data_access_log_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_data_access_log_patient_id FOREIGN KEY (patient_id)
                REFERENCES clinic.patients (id) ON DELETE RESTRICT,
            CONSTRAINT fk_data_access_log_actor_user_id FOREIGN KEY (actor_user_id)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_data_access_log_actor_role
                CHECK (actor_role IN ({ACTOR_ROLES})),
            CONSTRAINT ck_data_access_log_actor_type
                CHECK (actor_type IN ('USER', 'SYSTEM')),
            CONSTRAINT ck_data_access_log_resource_type
                CHECK (resource_type IN ({RESOURCE_TYPES})),
            CONSTRAINT ck_data_access_log_action
                CHECK (action IN ('READ', 'EXPORT', 'DECRYPT')),
            CONSTRAINT ck_data_access_log_outcome
                CHECK (outcome IN ('GRANTED', 'DENIED')),
            CONSTRAINT ck_data_access_log_purpose
                CHECK (purpose IN ('CARE', 'SELF', 'ADMINISTRATION', 'AI_PROCESSING'))
        );
        CREATE INDEX ix_data_access_log_clinic_occurred
            ON audit.data_access_log (clinic_id, occurred_at DESC);
        CREATE INDEX ix_data_access_log_patient
            ON audit.data_access_log (clinic_id, patient_id, occurred_at DESC);
        CREATE INDEX ix_data_access_log_actor
            ON audit.data_access_log (clinic_id, actor_user_id, occurred_at DESC);
        COMMENT ON TABLE audit.data_access_log IS
            'Every read of clinical data (PhiAccessAuditDecorator, P09); fail closed';

        CREATE TABLE audit.retention_policies (
            id uuid NOT NULL DEFAULT uuidv7(),
            clinic_id uuid,
            table_name varchar(80) NOT NULL,
            reference_column varchar(60) NOT NULL,
            retention_days integer,
            action varchar(9) NOT NULL DEFAULT 'DELETE',
            is_enabled boolean NOT NULL DEFAULT false,
            basis varchar(300),
            updated_by uuid,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_retention_policies PRIMARY KEY (id),
            CONSTRAINT fk_retention_policies_clinic_id FOREIGN KEY (clinic_id)
                REFERENCES clinic.clinics (id) ON DELETE RESTRICT,
            CONSTRAINT fk_retention_policies_updated_by FOREIGN KEY (updated_by)
                REFERENCES iam.users (id) ON DELETE RESTRICT,
            CONSTRAINT ck_retention_policies_table_name
                CHECK (table_name ~ '^[a-z_]+\\.[a-z_]+$'),
            CONSTRAINT ck_retention_policies_retention_days
                CHECK (retention_days > 0),
            CONSTRAINT ck_retention_policies_action
                CHECK (action IN ('DELETE', 'ANONYMIZE'))
        );
        CREATE UNIQUE INDEX ux_retention_policies_table
            ON audit.retention_policies (clinic_id, table_name) NULLS NOT DISTINCT;
        COMMENT ON TABLE audit.retention_policies IS
            'Retention period per table, as data; changed only by its definer function';
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION audit.audit_log_content(r audit.audit_log) RETURNS text
            LANGUAGE sql IMMUTABLE
            SET search_path = pg_catalog
            AS $$
                SELECT concat_ws(
                    chr(31),
                    r.clinic_id::text,
                    r.chain_seq::text,
                    to_char(r.occurred_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    coalesce(r.actor_user_id::text, ''),
                    coalesce(r.actor_role, ''),
                    r.actor_type,
                    r.action,
                    r.entity_type,
                    coalesce(r.entity_id::text, ''),
                    coalesce(r.entity_version::text, ''),
                    coalesce(r.reason, ''),
                    array_to_string(r.changed_fields, ','),
                    coalesce(r.request_id, ''),
                    coalesce(r.ip_hmac, '')
                )
            $$;
        COMMENT ON FUNCTION audit.audit_log_content(audit.audit_log) IS
            'Canonical content of an audit row: 0x1F separated, UTC ISO-8601';

        CREATE FUNCTION audit.compute_chain_hash() RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $$
            DECLARE
                last_seq bigint;
                last_hash char(64);
            BEGIN
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.clinic_id::text, 0));
                SELECT a.chain_seq, a.row_hash INTO last_seq, last_hash
                    FROM audit.audit_log AS a
                    WHERE a.clinic_id = NEW.clinic_id
                    ORDER BY a.chain_seq DESC
                    LIMIT 1;
                NEW.chain_seq := coalesce(last_seq, 0) + 1;
                NEW.prev_hash := coalesce(last_hash, '{GENESIS_HASH}');
                NEW.row_hash := encode(
                    sha256(convert_to(
                        NEW.prev_hash || audit.audit_log_content(NEW), 'UTF8'
                    )),
                    'hex'
                );
                RETURN NEW;
            END
            $$;
        CREATE TRIGGER trg_audit_log_chain BEFORE INSERT ON audit.audit_log
            FOR EACH ROW EXECUTE FUNCTION audit.compute_chain_hash();

        CREATE TRIGGER trg_audit_log_append_only
            BEFORE UPDATE OR DELETE ON audit.audit_log
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
        CREATE TRIGGER trg_data_access_log_append_only
            BEFORE UPDATE OR DELETE ON audit.data_access_log
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

        CREATE FUNCTION audit.append_audit_log(
            p_action varchar,
            p_entity_type varchar,
            p_entity_id uuid DEFAULT NULL,
            p_entity_version integer DEFAULT NULL,
            p_reason varchar DEFAULT NULL,
            p_changed_fields varchar[] DEFAULT '{{}}',
            p_request_id varchar DEFAULT NULL,
            p_ip_hmac char DEFAULT NULL,
            p_clinic_id uuid DEFAULT NULL
        ) RETURNS uuid
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_clinic uuid := coalesce(iam.app_clinic_id(), p_clinic_id);
                v_user uuid := iam.app_user_id();
                v_role varchar := iam.app_role();
                v_id uuid;
            BEGIN
                IF v_clinic IS NULL THEN
                    RAISE EXCEPTION 'audit.append_audit_log needs a clinic context';
                END IF;
                IF iam.app_clinic_id() IS NOT NULL
                   AND p_clinic_id IS NOT NULL
                   AND p_clinic_id <> iam.app_clinic_id() THEN
                    RAISE EXCEPTION 'audit.append_audit_log: clinic mismatch';
                END IF;
                INSERT INTO audit.audit_log (
                    clinic_id, chain_seq, actor_user_id, actor_role, actor_type,
                    action, entity_type, entity_id, entity_version, reason,
                    changed_fields, request_id, ip_hmac, prev_hash, row_hash
                ) VALUES (
                    v_clinic, 1, v_user, v_role,
                    CASE WHEN v_user IS NULL THEN 'SYSTEM' ELSE 'USER' END,
                    p_action, p_entity_type, p_entity_id, p_entity_version,
                    p_reason, coalesce(p_changed_fields, '{{}}'), p_request_id,
                    p_ip_hmac, '{GENESIS_HASH}', '{GENESIS_HASH}'
                ) RETURNING id INTO v_id;
                RETURN v_id;
            END
            $$;
        COMMENT ON FUNCTION audit.append_audit_log(
            varchar, varchar, uuid, integer, varchar, varchar[], varchar, char, uuid
        ) IS 'Only way for the app to write audit_log; actor from the session';

        CREATE FUNCTION audit.verify_chain(p_clinic_id uuid)
            RETURNS TABLE (broken_seq bigint, broken_id uuid)
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                r audit.audit_log;
                expected_prev char(64) := '{GENESIS_HASH}';
                expected_seq bigint := 1;
            BEGIN
                FOR r IN
                    SELECT * FROM audit.audit_log AS a
                    WHERE a.clinic_id = p_clinic_id
                    ORDER BY a.chain_seq
                LOOP
                    IF r.chain_seq <> expected_seq
                       OR r.prev_hash <> expected_prev
                       OR r.row_hash <> encode(
                           sha256(convert_to(
                               r.prev_hash || audit.audit_log_content(r), 'UTF8'
                           )),
                           'hex'
                       )
                    THEN
                        broken_seq := r.chain_seq;
                        broken_id := r.id;
                        RETURN NEXT;
                        RETURN;
                    END IF;
                    expected_prev := r.row_hash;
                    expected_seq := expected_seq + 1;
                END LOOP;
            END
            $$;
        COMMENT ON FUNCTION audit.verify_chain(uuid) IS
            'First broken row of the clinic hash chain, or no row when intact';

        CREATE FUNCTION audit.set_retention_policy(
            p_table_name varchar,
            p_reference_column varchar,
            p_retention_days integer,
            p_action varchar,
            p_is_enabled boolean,
            p_basis varchar,
            p_reason varchar,
            p_global boolean DEFAULT false
        ) RETURNS uuid
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
            DECLARE
                v_role varchar := iam.app_role();
                v_clinic uuid :=
                    CASE WHEN p_global THEN NULL ELSE iam.app_clinic_id() END;
                v_id uuid;
            BEGIN
                IF p_global AND v_role IS DISTINCT FROM 'PLATFORM_ADMIN' THEN
                    RAISE EXCEPTION 'only PLATFORM_ADMIN changes global retention';
                END IF;
                IF NOT p_global AND (v_role IS DISTINCT FROM 'CLINIC_ADMIN'
                                     OR v_clinic IS NULL) THEN
                    RAISE EXCEPTION 'only CLINIC_ADMIN changes clinic retention';
                END IF;
                INSERT INTO audit.retention_policies AS rp (
                    clinic_id, table_name, reference_column, retention_days,
                    action, is_enabled, basis, updated_by, updated_at
                ) VALUES (
                    v_clinic, p_table_name, p_reference_column, p_retention_days,
                    p_action, p_is_enabled, p_basis, iam.app_user_id(), now()
                )
                ON CONFLICT (clinic_id, table_name) DO UPDATE SET
                    reference_column = EXCLUDED.reference_column,
                    retention_days = EXCLUDED.retention_days,
                    action = EXCLUDED.action,
                    is_enabled = EXCLUDED.is_enabled,
                    basis = EXCLUDED.basis,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
                RETURNING rp.id INTO v_id;
                IF v_clinic IS NOT NULL THEN
                    PERFORM audit.append_audit_log(
                        'RETENTION_POLICY_CHANGED', 'retention_policy', v_id,
                        NULL, p_reason, ARRAY['retention_days', 'action', 'is_enabled']
                    );
                END IF;
                RETURN v_id;
            END
            $$;
        """
    )

    # Hand the functions to insumap_definer; it needs CREATE on the schema only
    # while the ownership changes (as in the iam definer functions).
    op.execute(
        """
        GRANT CREATE ON SCHEMA audit TO insumap_definer;
        ALTER FUNCTION audit.compute_chain_hash() OWNER TO insumap_definer;
        ALTER FUNCTION audit.append_audit_log(
            varchar, varchar, uuid, integer, varchar, varchar[], varchar, char, uuid
        ) OWNER TO insumap_definer;
        ALTER FUNCTION audit.verify_chain(uuid) OWNER TO insumap_definer;
        ALTER FUNCTION audit.set_retention_policy(
            varchar, varchar, integer, varchar, boolean, varchar, varchar, boolean
        ) OWNER TO insumap_definer;
        REVOKE CREATE ON SCHEMA audit FROM insumap_definer;
        """
    )

    op.execute(
        """
        ALTER TABLE audit.audit_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit.audit_log FORCE ROW LEVEL SECURITY;
        ALTER TABLE audit.data_access_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit.data_access_log FORCE ROW LEVEL SECURITY;
        ALTER TABLE audit.retention_policies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit.retention_policies FORCE ROW LEVEL SECURITY;

        -- RLS-5: insert only through the definer; read by the clinic admin.
        CREATE POLICY audit_log_insert_definer ON audit.audit_log FOR INSERT
            TO insumap_definer WITH CHECK (true);
        CREATE POLICY audit_log_select_definer ON audit.audit_log FOR SELECT
            TO insumap_definer USING (true);
        CREATE POLICY audit_log_select_clinic_admin ON audit.audit_log FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() = 'CLINIC_ADMIN'
            );

        -- RLS-1 with open insert in the session clinic.
        CREATE POLICY data_access_log_insert ON audit.data_access_log FOR INSERT
            WITH CHECK (clinic_id = iam.app_clinic_id());
        CREATE POLICY data_access_log_select_clinic_admin
            ON audit.data_access_log FOR SELECT
            USING (
                clinic_id = iam.app_clinic_id()
                AND iam.app_role() = 'CLINIC_ADMIN'
            );

        -- RLS-3: global seeds and the clinic own rows.
        CREATE POLICY retention_policies_select ON audit.retention_policies
            FOR SELECT USING (
                (clinic_id IS NULL OR clinic_id = iam.app_clinic_id())
                AND iam.app_role() IN ('CLINIC_ADMIN', 'SYSTEM')
            );
        CREATE POLICY retention_policies_write_definer ON audit.retention_policies
            FOR ALL TO insumap_definer USING (true) WITH CHECK (true);

        GRANT SELECT ON audit.audit_log TO insumap_app;
        GRANT SELECT, INSERT ON audit.data_access_log TO insumap_app;
        GRANT SELECT ON audit.retention_policies TO insumap_app;
        GRANT SELECT, INSERT ON audit.audit_log TO insumap_definer;
        GRANT SELECT, INSERT, UPDATE ON audit.retention_policies TO insumap_definer;
        GRANT SELECT ON audit.audit_log, audit.data_access_log,
            audit.retention_policies TO insumap_readonly;
        GRANT USAGE ON SCHEMA audit
            TO insumap_app, insumap_readonly, insumap_definer;

        REVOKE ALL ON FUNCTION audit.append_audit_log(
            varchar, varchar, uuid, integer, varchar, varchar[], varchar, char, uuid
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION audit.append_audit_log(
            varchar, varchar, uuid, integer, varchar, varchar[], varchar, char, uuid
        ) TO insumap_app;
        REVOKE ALL ON FUNCTION audit.verify_chain(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION audit.verify_chain(uuid) TO insumap_app;
        REVOKE ALL ON FUNCTION audit.set_retention_policy(
            varchar, varchar, integer, varchar, boolean, varchar, varchar, boolean
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION audit.set_retention_policy(
            varchar, varchar, integer, varchar, boolean, varchar, varchar, boolean
        ) TO insumap_app;
        """
    )


def downgrade() -> None:
    """Drop the audit tables and their functions (the schema stays)."""
    op.execute(
        """
        REVOKE USAGE ON SCHEMA audit
            FROM insumap_app, insumap_readonly, insumap_definer;
        DROP FUNCTION audit.set_retention_policy(
            varchar, varchar, integer, varchar, boolean, varchar, varchar, boolean
        );
        DROP FUNCTION audit.verify_chain(uuid);
        DROP FUNCTION audit.append_audit_log(
            varchar, varchar, uuid, integer, varchar, varchar[], varchar, char, uuid
        );
        DROP TABLE audit.retention_policies;
        DROP TABLE audit.data_access_log;
        DROP TRIGGER trg_audit_log_chain ON audit.audit_log;
        DROP FUNCTION audit.compute_chain_hash();
        DROP FUNCTION audit.audit_log_content(audit.audit_log);
        DROP TABLE audit.audit_log;
        """
    )
