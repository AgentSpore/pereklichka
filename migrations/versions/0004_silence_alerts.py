"""Persist one silence alert per ward and local day."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "silence_alerts",
        sa.Column(
            "ward_id", sa.Uuid(), sa.ForeignKey("wards.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("local_day", sa.Date(), primary_key=True),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("silence_alerts")
