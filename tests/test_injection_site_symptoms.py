"""monitoring.symptoms.injection_site and the LIPOHYPERTROPHY catalog seed."""

import psycopg
import pytest
from psycopg import errors

from tests.helpers import INJECTION_SITES, Tenant, act_as, make_tenant, scalar

LIPOHYPERTROPHY = (
    "SELECT id FROM care.symptom_catalog"
    " WHERE clinic_id IS NULL AND code = 'LIPOHYPERTROPHY'"
)
INSERT = (
    "INSERT INTO monitoring.symptoms (clinic_id, patient_id, recorded_by,"
    " client_record_id, occurred_at, symptom_id, injection_site)"
    " VALUES (%s, %s, %s, uuidv7(), now(), %s, %s) RETURNING id"
)


@pytest.fixture
def tenant(db: psycopg.Connection) -> Tenant:
    return make_tenant(db, "sintomas")


def insert_symptom(db: psycopg.Connection, t: Tenant, site: str | None) -> object:
    return scalar(
        db,
        INSERT,
        (
            t.clinic_id,
            t.patient_id,
            t.patient_user_id,
            scalar(db, LIPOHYPERTROPHY),
            site,
        ),
    )


def test_lipohypertrophy_is_seeded_as_a_global_non_alarm_symptom(
    db: psycopg.Connection,
) -> None:
    row = db.execute(
        "SELECT label_es, is_alarm, is_active FROM care.symptom_catalog"
        " WHERE clinic_id IS NULL AND code = 'LIPOHYPERTROPHY'"
    ).fetchone()
    assert row == ("Lipohipertrofia", False, True)


def test_every_clinic_role_reads_the_seed(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    for role in ("CLINIC_ADMIN", "DOCTOR", "PATIENT", "SYSTEM"):
        act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.doctor_id, role=role)
        assert scalar(db, LIPOHYPERTROPHY) is not None, role
        db.execute("RESET ROLE")


@pytest.mark.parametrize("site", INJECTION_SITES)
def test_every_site_of_the_catalog_is_accepted(
    db: psycopg.Connection, tenant: Tenant, site: str
) -> None:
    insert_symptom(db, tenant, site)


def test_a_symptom_without_site_stays_valid(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    insert_symptom(db, tenant, None)


@pytest.mark.parametrize("site", ["ABDOMEN", "arm_left", ""])
def test_a_site_outside_the_catalog_is_rejected(
    db: psycopg.Connection, tenant: Tenant, site: str
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_symptom(db, tenant, site)


def test_the_patient_records_and_reads_the_site_of_a_symptom(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    act_as(
        db,
        clinic_id=tenant.clinic_id,
        user_id=tenant.patient_user_id,
        role="PATIENT",
    )
    symptom_id = insert_symptom(db, tenant, "THIGH_RIGHT")
    site = scalar(
        db,
        "SELECT injection_site FROM monitoring.symptoms WHERE id = %s",
        (symptom_id,),
    )
    assert site == "THIGH_RIGHT"


def test_the_site_of_a_symptom_cannot_be_edited(
    db: psycopg.Connection, tenant: Tenant
) -> None:
    symptom_id = insert_symptom(db, tenant, "ARM_RIGHT")
    with pytest.raises(errors.RaiseException), db.transaction():
        db.execute(
            "UPDATE monitoring.symptoms SET injection_site = 'ARM_LEFT' WHERE id = %s",
            (symptom_id,),
        )
