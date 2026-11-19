"""iam.clinic_admin_contact(): the email of a clinic admin, for PLATFORM_ADMIN only."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import Tenant, act_as, add_membership, make_tenant, make_user

CONTACT = "SELECT email, full_name FROM iam.clinic_admin_contact(%s, %s)"


@pytest.fixture
def platform_admin(db: psycopg.Connection) -> UUID:
    user_id = make_user(db, "platform-admin@example.test")
    add_membership(db, user_id, None, "PLATFORM_ADMIN")
    return user_id


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "contacto")


def test_platform_admin_reads_the_admin_contact(
    db: psycopg.Connection, platform_admin: UUID, tenant: Tenant
) -> None:
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)

    row = db.execute(CONTACT, (tenant.clinic_id, tenant.admin_id)).fetchone()

    assert row is not None
    assert row[0] == "admin-contacto@example.test"
    assert row[1]


def test_a_user_that_is_not_an_admin_of_the_clinic_is_not_found(
    db: psycopg.Connection, platform_admin: UUID, tenant: Tenant
) -> None:
    act_as(db, role="PLATFORM_ADMIN", user_id=platform_admin)

    with pytest.raises(errors.RaiseException, match="not found"):
        db.execute(CONTACT, (tenant.clinic_id, tenant.doctor_id))


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "DOCTOR", "PATIENT"])
def test_other_roles_are_refused(
    db: psycopg.Connection, tenant: Tenant, role: str
) -> None:
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.admin_id, role=role)

    with pytest.raises(errors.RaiseException):
        db.execute(CONTACT, (tenant.clinic_id, tenant.admin_id))
