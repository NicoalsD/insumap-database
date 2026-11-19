"""Add injection_site and injection_spot to monitoring.insulin_doses.

RF-101: the patient may say where the dose went. injection_site is one of the
eight zones of the body (right and left are the patient's own) and
injection_spot is the point inside the zone, from 1 to 4, used to rotate it. A
spot only makes sense inside a site, so it cannot be set alone. Both are
optional: a dose recorded without them stays valid.

The table is append-only and keeps its grants (SELECT and INSERT at table
level), so the new columns need no new privilege.

Revision ID: 9784a458ce58
Revises: 5d7bebe750c2
Create Date: 2026-11-19 18:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9784a458ce58"
down_revision: str | None = "5d7bebe750c2"
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
SITE_LIST = ", ".join(f"'{site}'" for site in SITES)


def upgrade() -> None:
    """Add the columns and their checks."""
    op.execute(
        f"""
        ALTER TABLE monitoring.insulin_doses
            ADD COLUMN injection_site varchar(14),
            ADD COLUMN injection_spot smallint,
            ADD CONSTRAINT ck_insulin_doses_injection_site CHECK (
                injection_site IN ({SITE_LIST})
            ),
            ADD CONSTRAINT ck_insulin_doses_injection_spot CHECK (
                injection_spot BETWEEN 1 AND 4
            ),
            ADD CONSTRAINT ck_insulin_doses_injection_spot_site CHECK (
                injection_spot IS NULL OR injection_site IS NOT NULL
            );
        COMMENT ON COLUMN monitoring.insulin_doses.injection_site IS
            'Zone chosen by the patient; right and left are the patient''s own';
        COMMENT ON COLUMN monitoring.insulin_doses.injection_spot IS
            'Point inside the zone, 1 to 4, to rotate it; null when no site';
        """
    )


def downgrade() -> None:
    """Drop the checks and the columns."""
    op.execute(
        """
        ALTER TABLE monitoring.insulin_doses
            DROP CONSTRAINT ck_insulin_doses_injection_spot_site,
            DROP CONSTRAINT ck_insulin_doses_injection_spot,
            DROP CONSTRAINT ck_insulin_doses_injection_site,
            DROP COLUMN injection_spot,
            DROP COLUMN injection_site;
        """
    )
