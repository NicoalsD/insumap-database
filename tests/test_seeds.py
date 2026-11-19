"""Reference seeds: roles, permission catalog and matrix."""

import psycopg
import pytest

from tests.helpers import APP_ROLE, act_as, scalar

ROLES_WITH_MFA = {
    "PLATFORM_ADMIN": True,
    "CLINIC_ADMIN": True,
    "DOCTOR": True,
    "PATIENT": False,
}
PERMISSION_CATALOG_SIZE = 69
PERMISSIONS_PER_ROLE = {
    "PLATFORM_ADMIN": 7,
    "CLINIC_ADMIN": 27,
    "DOCTOR": 26,
    "PATIENT": 21,
}


def test_roles_are_seeded(db: psycopg.Connection) -> None:
    rows = db.execute("SELECT code, requires_mfa FROM iam.roles").fetchall()
    assert dict(rows) == ROLES_WITH_MFA


def test_permission_catalog_is_seeded(db: psycopg.Connection) -> None:
    assert scalar(db, "SELECT count(*) FROM iam.permissions") == PERMISSION_CATALOG_SIZE


def test_matrix_per_role(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT r.code, count(*) FROM iam.role_permissions AS rp"
        " JOIN iam.roles AS r ON r.id = rp.role_id GROUP BY r.code"
    ).fetchall()
    assert dict(rows) == PERMISSIONS_PER_ROLE


@pytest.mark.parametrize(
    ("role", "permission", "granted"),
    [
        ("CLINIC_ADMIN", "credentials.issue", True),
        ("CLINIC_ADMIN", "patients.read_assigned", False),
        ("PATIENT", "consents.manage_own", True),
        ("DOCTOR", "patients.read_assigned", True),
        ("PLATFORM_ADMIN", "clinics.manage", True),
        ("PLATFORM_ADMIN", "patients.read_roster", False),
    ],
)
def test_app_reads_permissions_of_a_role(
    db: psycopg.Connection, role: str, permission: str, granted: bool
) -> None:
    act_as(db, APP_ROLE)
    exists = scalar(
        db,
        "SELECT EXISTS (SELECT 1 FROM iam.role_permissions AS rp"
        " JOIN iam.roles AS r ON r.id = rp.role_id"
        " JOIN iam.permissions AS p ON p.id = rp.permission_id"
        " WHERE r.code = %s AND p.code = %s)",
        (role, permission),
    )
    assert exists is granted
