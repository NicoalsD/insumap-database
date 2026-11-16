"""Smoke tests for the migration chain itself."""

from alembic import command
from alembic.config import Config


def test_upgrade_and_downgrade_run_cleanly(alembic_config: Config) -> None:
    """Upgrade to head, downgrade to base, and upgrade again without errors."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
