"""Relatives bot, invitations and durable delivery queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
TZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column(
        "wards",
        sa.Column("escalation_minutes", sa.SmallInteger(), nullable=False, server_default="30"),
    )
    op.add_column("check_ins", sa.Column("completed_at", TZ, nullable=True))
    # Historical answers have no reliable completion marker; the new queue stays empty.
    op.create_unique_constraint("uq_relatives_chat_id", "relatives", ["chat_id"])
    op.create_table(
        "invitations",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "family_id", sa.Uuid(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", TZ, nullable=False),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_key", sa.String(150), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(4096), nullable=False),
        sa.Column("available_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("delivered_at", TZ, nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("event_key", "chat_id", name="uq_outbox_event_chat"),
    )
    op.create_index(
        "ix_outbox_pending",
        "outbox",
        ["available_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("invitations")
    op.drop_constraint("uq_relatives_chat_id", "relatives", type_="unique")
    op.drop_column("check_ins", "completed_at")
    op.drop_column("wards", "escalation_minutes")
