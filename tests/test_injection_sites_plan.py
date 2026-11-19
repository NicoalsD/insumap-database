"""care.treatment_plan_items.injection_sites."""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from tests.helpers import (
    INJECTION_SITES,
    CareRows,
    Tenant,
    act_as,
    make_tenant,
    populate_care,
    scalar,
)

INSERT = (
    "INSERT INTO care.treatment_plan_items (clinic_id, plan_id, item_type,"
    " insulin_kind, name, injection_sites)"
    " VALUES (%s, %s, 'INSULIN', 'BASAL', 'Basal', %s) RETURNING id"
)


@pytest.fixture
def world(db: psycopg.Connection) -> tuple[Tenant, CareRows]:
    tenant = make_tenant(db, "plan")
    return tenant, populate_care(db, tenant)


def insert_item(
    db: psycopg.Connection, world: tuple[Tenant, CareRows], sites: list[str] | None
) -> UUID:
    tenant, care = world
    value = scalar(db, INSERT, (tenant.clinic_id, care.draft_plan_id, sites))
    assert isinstance(value, UUID)
    return value


def test_null_means_all_the_sites(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    item_id = insert_item(db, world, None)
    stored = scalar(
        db,
        "SELECT injection_sites FROM care.treatment_plan_items WHERE id = %s",
        (item_id,),
    )
    assert stored is None


def test_existing_items_have_no_restriction(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    _, care = world
    stored = scalar(
        db,
        "SELECT injection_sites FROM care.treatment_plan_items WHERE id = %s",
        (care.item_id,),
    )
    assert stored is None


def test_a_list_of_catalog_sites_is_stored(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    sites = ["ABDOMEN_RIGHT", "THIGH_LEFT"]
    item_id = insert_item(db, world, sites)
    stored = scalar(
        db,
        "SELECT injection_sites FROM care.treatment_plan_items WHERE id = %s",
        (item_id,),
    )
    assert stored == sites


def test_the_whole_catalog_is_accepted(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    insert_item(db, world, list(INJECTION_SITES))


def test_an_empty_list_is_rejected(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_item(db, world, [])


@pytest.mark.parametrize("sites", [["ABDOMEN"], ["ARM_LEFT", "LEG_RIGHT"]])
def test_a_site_outside_the_catalog_is_rejected(
    db: psycopg.Connection, world: tuple[Tenant, CareRows], sites: list[str]
) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_item(db, world, sites)


def test_the_doctor_edits_the_sites_of_a_draft_item(
    db: psycopg.Connection, world: tuple[Tenant, CareRows]
) -> None:
    tenant, care = world
    act_as(db, clinic_id=tenant.clinic_id, user_id=tenant.doctor_id, role="DOCTOR")
    db.execute(
        "UPDATE care.treatment_plan_items SET injection_sites = %s WHERE id = %s",
        (["ARM_LEFT"], care.item_id),
    )
    stored = scalar(
        db,
        "SELECT injection_sites FROM care.treatment_plan_items WHERE id = %s",
        (care.item_id,),
    )
    assert stored == ["ARM_LEFT"]
