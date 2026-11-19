"""Add injection_site to monitoring.symptoms and seed LIPOHYPERTROPHY.

RF-101: a lipohypertrophy is reported per site, and the suggestion of the next
site leaves that zone out for 90 days. injection_site uses the same catalog as
monitoring.insulin_doses and is null for any other symptom (the rule that a
LIPOHYPERTROPHY report needs a site is checked by the API, because the symptom
code lives in care.symptom_catalog).

Seed (global row of care.symptom_catalog, clinic_id NULL): LIPOHYPERTROPHY,
not an alarm. The migrator may write the catalog through its own policy.

Revision ID: 9d037ad9e5fe
Revises: 9784a458ce58
Create Date: 2026-11-19 18:20:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9d037ad9e5fe"
down_revision: str | None = "9784a458ce58"
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
    """Add the column and its check, then seed the catalog row."""
    op.execute(
        f"""
        ALTER TABLE monitoring.symptoms
            ADD COLUMN injection_site varchar(14),
            ADD CONSTRAINT ck_symptoms_injection_site CHECK (
                injection_site IN ({SITE_LIST})
            );
        COMMENT ON COLUMN monitoring.symptoms.injection_site IS
            'Zone affected by a lipohypertrophy; null for other symptoms';

        INSERT INTO care.symptom_catalog (clinic_id, code, label_es, is_alarm)
        VALUES (NULL, 'LIPOHYPERTROPHY', 'Lipohipertrofia', false)
        ON CONFLICT (clinic_id, code) DO NOTHING;
        """
    )


def downgrade() -> None:
    """Remove the seed, the check and the column."""
    op.execute(
        """
        DELETE FROM care.symptom_catalog
            WHERE clinic_id IS NULL AND code = 'LIPOHYPERTROPHY';
        ALTER TABLE monitoring.symptoms
            DROP CONSTRAINT ck_symptoms_injection_site,
            DROP COLUMN injection_site;
        """
    )
