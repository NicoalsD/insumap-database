"""Add injection_sites to care.treatment_plan_items.

RF-101: the doctor may restrict the zones a plan item can use. NULL means all
the sites; a list limits the suggestion to those sites. An empty list would say
"no site at all", which is not a valid prescription, so it is rejected, and
every element must belong to the catalog.

The items keep their grants (table level) and their DRAFT-only edit rule; the
snapshots of a plan are jsonb, so they carry the new field without a change.

Revision ID: 54ab7d646e8e
Revises: 9d037ad9e5fe
Create Date: 2026-11-19 18:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "54ab7d646e8e"
down_revision: str | None = "9d037ad9e5fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SITES = (
    "ABDOMEN_RIGHT",
    "ABDOMEN_LEFT",
    "THIGH_RIGHT",
    "THIGH_LEFT",
    "ARM_RIGHT",
    "ARM_LEFT",
    "BUTTOCK_RIGHT",
    "BUTTOCK_LEFT",
)
SITE_ARRAY = "ARRAY[" + ", ".join(f"'{site}'" for site in SITES) + "]::varchar[]"


def upgrade() -> None:
    """Add the column and its check."""
    op.execute(
        f"""
        ALTER TABLE care.treatment_plan_items
            ADD COLUMN injection_sites varchar(14)[],
            ADD CONSTRAINT ck_treatment_plan_items_injection_sites CHECK (
                cardinality(injection_sites) > 0
                AND injection_sites <@ {SITE_ARRAY}
            );
        COMMENT ON COLUMN care.treatment_plan_items.injection_sites IS
            'Sites the doctor allows; null means all of them';
        """
    )


def downgrade() -> None:
    """Drop the check and the column."""
    op.execute(
        """
        ALTER TABLE care.treatment_plan_items
            DROP CONSTRAINT ck_treatment_plan_items_injection_sites,
            DROP COLUMN injection_sites;
        """
    )
