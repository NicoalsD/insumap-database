"""Add bmi and pregnancy_status to clinic.patients.

The injection site rules (RF-101) need two facts about the patient: the body
mass index, which picks the needle note, and the pregnancy status, which
removes the abdomen from the suggestions. Both are optional for the clinic: bmi
is null until it is known and pregnancy_status defaults to UNKNOWN. No endpoint
writes them yet; the clinic import is the expected source.

The table keeps its row level security and the table level grant of
insumap_app. insumap_readonly reads clinic.patients by column (it never sees the
document material), so the two new columns are added to that grant.

Revision ID: 5d7bebe750c2
Revises: d2b6e9f41c03
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5d7bebe750c2"
down_revision: str | None = "d2b6e9f41c03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the columns, their checks and the read-only grant."""
    op.execute(
        """
        ALTER TABLE clinic.patients
            ADD COLUMN bmi numeric(4,1),
            ADD COLUMN pregnancy_status varchar(8) NOT NULL DEFAULT 'UNKNOWN',
            ADD CONSTRAINT ck_patients_bmi CHECK (bmi BETWEEN 10 AND 80),
            ADD CONSTRAINT ck_patients_pregnancy_status CHECK (
                pregnancy_status IN ('NONE', 'PREGNANT', 'UNKNOWN')
            );
        COMMENT ON COLUMN clinic.patients.bmi IS
            'Body mass index in kg/m2; null when unknown';
        COMMENT ON COLUMN clinic.patients.pregnancy_status IS
            'NONE, PREGNANT or UNKNOWN; the injection site rules read it';

        GRANT SELECT (bmi, pregnancy_status) ON clinic.patients
            TO insumap_readonly;
        """
    )


def downgrade() -> None:
    """Revoke the grant and drop the columns."""
    op.execute(
        """
        REVOKE SELECT (bmi, pregnancy_status) ON clinic.patients
            FROM insumap_readonly;
        ALTER TABLE clinic.patients
            DROP CONSTRAINT ck_patients_pregnancy_status,
            DROP CONSTRAINT ck_patients_bmi,
            DROP COLUMN pregnancy_status,
            DROP COLUMN bmi;
        """
    )
