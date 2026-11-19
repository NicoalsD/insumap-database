"""alerts.clinic_alert_summary(): counts for the clinic administrator only."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import Tenant, act_as, make_tenant, populate_alerts

SUMMARY = (
    "SELECT doctor_id, open_count, escalated_count, unattended_count"
    " FROM alerts.clinic_alert_summary() ORDER BY doctor_id NULLS FIRST"
)


@pytest.fixture
def tenants(db: psycopg.Connection) -> tuple[Tenant, Tenant]:
    a, b = make_tenant(db, "a"), make_tenant(db, "b")
    populate_alerts(db, a)
    populate_alerts(db, b)
    db.execute(
        "UPDATE clinic.care_team_assignments SET is_primary = true WHERE id = %s",
        (a.assignment_id,),
    )
    return a, b


def test_admin_gets_the_clinic_totals_and_the_primary_doctor_count(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.admin_id, role="CLINIC_ADMIN")

    rows = db.execute(SUMMARY).fetchall()

    assert rows[0] == (None, 1, 0, 1)
    assert (a.doctor_id, 0, 0, 1) in rows


def test_counts_are_isolated_by_clinic(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant]
) -> None:
    _, b = tenants
    act_as(db, clinic_id=b.clinic_id, user_id=b.admin_id, role="CLINIC_ADMIN")

    totals = db.execute(SUMMARY).fetchall()[0]

    assert totals == (None, 1, 0, 1)


@pytest.mark.parametrize("role", ["DOCTOR", "PATIENT", "PLATFORM_ADMIN"])
def test_other_roles_are_refused(
    db: psycopg.Connection, tenants: tuple[Tenant, Tenant], role: str
) -> None:
    a, _ = tenants
    act_as(db, clinic_id=a.clinic_id, user_id=a.doctor_id, role=role)

    with pytest.raises(errors.RaiseException):
        db.execute(SUMMARY)
