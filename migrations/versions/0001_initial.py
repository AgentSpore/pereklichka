"""Initial schema: families, wards, relatives, link codes, check-ins.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table("families", sa.Column("id", sa.Uuid(), primary_key=True))
    op.create_table(
        "wards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "family_id", sa.Uuid(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("checkin_hour", sa.SmallInteger(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True, unique=True),
        sa.Column("consent_at", TZ, nullable=True),
    )
    op.create_table(
        "relatives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "family_id", sa.Uuid(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("alert_order", sa.SmallInteger(), nullable=False),
    )
    op.create_table(
        "link_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ward_id", sa.Uuid(), sa.ForeignKey("wards.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("used_at", TZ, nullable=True),
    )
    op.create_index(
        "uq_link_codes_unused_code",
        "link_codes",
        ["code"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_table(
        "check_ins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ward_id", sa.Uuid(), sa.ForeignKey("wards.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("at", TZ, nullable=False),
        sa.Column("wellbeing", sa.String(1024), nullable=False),
        sa.Column("meds_taken", sa.Boolean(), nullable=True),
        sa.Column("needs", sa.String(1024), nullable=True),
    )
    op.create_index("ix_check_ins_ward_id", "check_ins", ["ward_id"])


def downgrade() -> None:
    for table in ("check_ins", "link_codes", "relatives", "wards", "families"):
        op.drop_table(table)
