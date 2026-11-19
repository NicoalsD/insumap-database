"""clinic.patients: bmi and pregnancy_status."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import READONLY_ROLE, Tenant, act_as, make_tenant, scalar


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "bmi")


def test_defaults_are_unknown_and_null(db: psycopg.Connection, tenant: Tenant) -> None:
    row = db.execute(
        "SELECT bmi, pregnancy_status FROM clinic.patients WHERE id = %s",
        (tenant.patient_id,),
    ).fetchone()
    assert row == (None, "UNKNOWN")


@pytest.mark.parametrize("bmi", ["10", "24.5", "80"])
def test_bmi_inside_the_range_is_accepted(
    db: psycopg.Connection, tenant: Tenant, bmi: str
) -> None:
    db.execute(
        "UPDATE clinic.patients SET bmi = %s WHERE id = %s", (bmi, tenant.patient_id)
    )
    stored = scalar(
        db, "SELECT bmi::text FROM clinic.patients WHERE id = %s", (tenant.patient_id,)
    )
    assert stored == f"{float(bmi):.1f}"


@pytest.mark.parametrize("bmi", ["9.9", "80.1", "0", "-3"])
def test_bmi_outside_the_range_is_rejected(
    db: psycopg.Connection, tenant: Tenant, bmi: str
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "UPDATE clinic.patients SET bmi = %s WHERE id = %s",
            (bmi, tenant.patient_id),
        )


@pytest.mark.parametrize("status", ["NONE", "PREGNANT", "UNKNOWN"])
def test_pregnancy_status_catalog_is_accepted(
    db: psycopg.Connection, tenant: Tenant, status: str
) -> None:
    db.execute(
        "UPDATE clinic.patients SET pregnancy_status = %s WHERE id = %s",
        (status, tenant.patient_id),
    )


@pytest.mark.parametrize("status", ["YES", "none", ""])
def test_pregnancy_status_outside_the_catalog_is_rejected(
    db: psycopg.Connection, tenant: Tenant, status: str
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            "UPDATE clinic.patients SET pregnancy_status = %s WHERE id = %s",
            (status, tenant.patient_id),
        )


def test_pregnancy_status_cannot_be_null(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    with pytest.raises(errors.NotNullViolation), db.transaction():
        db.execute(
            "UPDATE clinic.patients SET pregnancy_status = NULL WHERE id = %s",
            (tenant.patient_id,),
        )


def test_clinic_admin_updates_the_columns(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.admin_id, role="CLINIC_ADMIN")
    db.execute(
        "UPDATE clinic.patients SET bmi = 27.3, pregnancy_status = 'PREGNANT'"
        " WHERE id = %s",
        (tenant.patient_id,),
    )
    row = db.execute(
        "SELECT bmi, pregnancy_status FROM clinic.patients WHERE id = %s",
        (tenant.patient_id,),
    ).fetchone()
    assert row is not None
    assert str(row[0]) == "27.3"
    assert row[1] == "PREGNANT"


def test_readonly_reads_the_new_columns(db: psycopg.Connection, tenant: Tenant) -> None:
    act_as(
        db,
        READONLY_ROLE,
        clinic_id=tenant.clinic_id,
        user_id=tenant.admin_id,
        role="CLINIC_ADMIN",
    )
    row = db.execute(
        "SELECT bmi, pregnancy_status FROM clinic.patients WHERE id = %s",
        (tenant.patient_id,),
    ).fetchone()
    assert row == (None, "UNKNOWN")
