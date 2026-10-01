"""Failed link-code attempts and the consent text version.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wards", sa.Column("consent_version", sa.String(64), nullable=True))
    op.create_table(
        "link_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("application_id", sa.String(64), nullable=False),
        sa.Column("at", TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_link_attempts_application_at", "link_attempts", ["application_id", "at"])


def downgrade() -> None:
    op.drop_table("link_attempts")
    op.drop_column("wards", "consent_version")
