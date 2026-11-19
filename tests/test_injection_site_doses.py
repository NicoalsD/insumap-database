"""monitoring.insulin_doses: injection_site and injection_spot."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import INJECTION_SITES, Tenant, act_as, make_tenant, scalar

INSERT = (
    "INSERT INTO monitoring.insulin_doses (clinic_id, patient_id, recorded_by,"
    " client_record_id, administered_at, insulin_kind, product_name, units,"
    " injection_site, injection_spot)"
    " VALUES (%s, %s, %s, uuidv7(), now(), 'BASAL', 'Producto', 5, %s, %s)"
    " RETURNING id"
)


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "dosis")


def insert_dose(
    db: psycopg.Connection, t: Tenant, site: str | None, spot: int | None
) -> object:
    return scalar(
        db, INSERT, (t.clinic_id, t.patient_id, t.patient_user_id, site, spot)
    )


@pytest.mark.parametrize("site", INJECTION_SITES)
def test_every_site_of_the_catalog_is_accepted(
    db: psycopg.Connection, tenant: Tenant, site: str
) -> None:
    insert_dose(db, tenant, site, 1)


@pytest.mark.parametrize("spot", [1, 2, 3, 4])
def test_spots_one_to_four_are_accepted(
    db: psycopg.Connection, tenant: Tenant, spot: int
) -> None:
    insert_dose(db, tenant, "THIGH_LEFT", spot)


def test_a_dose_without_site_and_spot_stays_valid(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    insert_dose(db, tenant, None, None)
    insert_dose(db, tenant, "ARM_RIGHT", None)


@pytest.mark.parametrize("site", ["ABDOMEN", "abdomen_right", "LEG_LEFT", ""])
def test_a_site_outside_the_catalog_is_rejected(
    db: psycopg.Connection, tenant: Tenant, site: str
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_dose(db, tenant, site, 1)


@pytest.mark.parametrize("spot", [0, 5, -1])
def test_a_spot_outside_the_range_is_rejected(
    db: psycopg.Connection, tenant: Tenant, spot: int
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_dose(db, tenant, "ARM_LEFT", spot)


def test_a_spot_without_a_site_is_rejected(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_dose(db, tenant, None, 2)


def test_the_patient_records_and_reads_its_own_site(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(
        db,
        clinic_id=tenant.clinic_id,
        user_id=tenant.patient_user_id,
        role="PATIENT",
    )
    dose_id = insert_dose(db, tenant, "BUTTOCK_RIGHT", 3)
    row = db.execute(
        "SELECT injection_site, injection_spot FROM monitoring.insulin_doses"
        " WHERE id = %s",
        (dose_id,),
    ).fetchone()
    assert row == ("BUTTOCK_RIGHT", 3)


def test_the_site_cannot_be_edited_afterwards(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    dose_id = insert_dose(db, tenant, "ARM_RIGHT", 1)
    with pytest.raises(errors.RaiseException), db.transaction():
        db.execute(
            "UPDATE monitoring.insulin_doses SET injection_site = 'ARM_LEFT'"
            " WHERE id = %s",
            (dose_id,),
        )
