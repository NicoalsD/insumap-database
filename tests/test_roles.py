"""SQL roles: attributes, object ownership and least privilege."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import APP_ROLE, READONLY_ROLE, act_as, scalar

APPLICATION_ROLES = ("insumap_migrator", "insumap_app", "insumap_readonly")
OWNER = "insumap_migrator"
DEFINER = "insumap_definer"
DEFINER_FUNCTIONS = (
    "iam.lookup_login_identity(text)",
    "iam.lookup_invitation_by_code_hash(text)",
    "iam.lookup_refresh_token(text)",
    "clinic.current_patient_id()",
    "clinic.on_clinic_suspended()",
)
SECRET_COLUMNS = (
    ("iam.users", "password_hash"),
    ("iam.credential_invitations", "code_hash"),
    ("clinic.patients", "document_hash"),
    ("clinic.patients", "document_ciphertext"),
)
READONLY_FORBIDDEN_TABLES = (
    "iam.auth_challenges",
    "iam.login_attempts",
    "iam.login_lockouts",
    "iam.rate_limit_buckets",
    "iam.refresh_tokens",
    "iam.mfa_factors",
    "iam.password_history",
)


@pytest.mark.parametrize("role", APPLICATION_ROLES)
def test_application_roles_are_not_privileged(
    db: psycopg.Connection, role: str
) -> None:
    row = db.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin"
        " FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    assert row == (False, False, False, False, True)


def test_definer_role_cannot_login_nor_bypass_rls(db: psycopg.Connection) -> None:
    row = db.execute(
        "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
        (DEFINER,),
    ).fetchone()
    assert row == (False, False, False)


def test_app_session_is_not_superuser(db: psycopg.Connection) -> None:
    act_as(db, APP_ROLE)
    row = db.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row == (False, False)


def test_migrator_owns_every_table_and_schema(db: psycopg.Connection) -> None:
    owners = db.execute(
        "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class AS c"
        " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
        " WHERE n.nspname IN ('iam', 'clinic') AND c.relkind IN ('r', 'i')"
        " UNION SELECT DISTINCT pg_get_userbyid(nspowner) FROM pg_namespace"
        " WHERE nspname IN ('iam', 'clinic', 'audit')"
    ).fetchall()
    assert owners == [(OWNER,)]


@pytest.mark.parametrize("function", DEFINER_FUNCTIONS)
def test_definer_functions_fix_search_path(
    db: psycopg.Connection, function: str
) -> None:
    row = db.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig"
        " FROM pg_proc WHERE oid = %s::regprocedure",
        (function,),
    ).fetchone()
    assert row is not None
    owner, security_definer, config = row
    assert owner == DEFINER
    assert security_definer is True
    assert any(setting.startswith("search_path=pg_catalog") for setting in config)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE iam.intruder (id integer)",
        "CREATE TABLE clinic.intruder (id integer)",
        "CREATE TABLE public.intruder (id integer)",
        "CREATE SCHEMA intruder",
        "ALTER TABLE iam.users ADD COLUMN intruder integer",
        "DROP TABLE clinic.patients",
        "TRUNCATE iam.security_events",
        "CREATE FUNCTION iam.intruder() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
    ],
)
def test_app_cannot_run_ddl(db: psycopg.Connection, statement: str) -> None:
    act_as(db, APP_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(statement)


@pytest.mark.parametrize(
    ("table", "privilege"),
    [
        ("iam.users", "DELETE"),
        ("iam.security_events", "UPDATE"),
        ("iam.security_events", "DELETE"),
        ("iam.login_attempts", "UPDATE"),
        ("iam.roles", "INSERT"),
        ("iam.role_permissions", "DELETE"),
        ("clinic.patient_consents", "UPDATE"),
        ("clinic.patient_consents", "DELETE"),
        ("clinic.privacy_notice_versions", "DELETE"),
    ],
)
def test_app_lacks_forbidden_dml(
    db: psycopg.Connection, table: str, privilege: str
) -> None:
    assert (
        scalar(
            db, "SELECT has_table_privilege(%s, %s, %s)", (APP_ROLE, table, privilege)
        )
        is False
    )


def test_app_updates_only_allowed_columns(db: psycopg.Connection) -> None:
    def can_update(table: str, column: str) -> object:
        return scalar(
            db,
            "SELECT has_column_privilege(%s, %s, %s, 'UPDATE')",
            (APP_ROLE, table, column),
        )

    assert can_update("iam.auth_challenges", "consumed_at") is True
    assert can_update("iam.auth_challenges", "token_hash") is False
    assert can_update("iam.rate_limit_buckets", "hits") is True
    assert can_update("iam.rate_limit_buckets", "key_hmac") is False
    assert can_update("clinic.privacy_notice_versions", "is_current") is True
    assert can_update("clinic.privacy_notice_versions", "content") is False


@pytest.mark.parametrize(("table", "column"), SECRET_COLUMNS)
def test_readonly_cannot_read_secret_columns(
    db: psycopg.Connection, table: str, column: str
) -> None:
    act_as(db, READONLY_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(f"SELECT {column} FROM {table}")


@pytest.mark.parametrize("table", READONLY_FORBIDDEN_TABLES)
def test_readonly_cannot_read_technical_tables(
    db: psycopg.Connection, table: str
) -> None:
    act_as(db, READONLY_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(f"SELECT 1 FROM {table}")


@pytest.mark.parametrize(
    "call",
    [
        "SELECT * FROM iam.lookup_login_identity('a@example.test')",
        "SELECT * FROM iam.lookup_invitation_by_code_hash(repeat('a', 64))",
        "SELECT * FROM iam.lookup_refresh_token(repeat('a', 64))",
    ],
)
def test_readonly_cannot_execute_lookups(db: psycopg.Connection, call: str) -> None:
    act_as(db, READONLY_ROLE)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute(call)
