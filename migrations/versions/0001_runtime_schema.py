"""runtime schema

Revision ID: 0001_runtime_schema
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_runtime_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column(
            "display_name", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "payments",
        sa.Column("charge_id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="paid"
        ),
        sa.Column(
            "is_recurring",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_first_recurring",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "subscription_expiration_date",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("provider_charge_id", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('paid','refunded','chargeback')",
            name="payments_status_check",
        ),
    )
    op.create_table(
        "refunds",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "charge_id",
            sa.Text(),
            sa.ForeignKey("payments.charge_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('requested','completed','failed')",
            name="refunds_status_check",
        ),
    )
    op.create_table(
        "subscriptions",
        sa.Column(
            "charge_id",
            sa.Text(),
            sa.ForeignKey("payments.charge_id"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("period_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True, unique=True),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','done','dead')",
            name="jobs_status_check",
        ),
    )
    op.create_table(
        "broadcasts",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_user_id",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "sent", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "failed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed')",
            name="broadcasts_status_check",
        ),
    )
    op.create_table(
        "broadcast_deliveries",
        sa.Column(
            "broadcast_id",
            sa.Uuid(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("broadcast_id", "chat_id"),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed')",
            name="broadcast_deliveries_status_check",
        ),
    )
    op.create_index("jobs_ready_idx", "jobs", ["status", "run_at"])
    op.create_index("broadcasts_status_idx", "broadcasts", ["status", "created_at"])
    op.create_index("broadcast_deliveries_chat_idx", "broadcast_deliveries", ["chat_id"])


def downgrade() -> None:
    op.drop_index("broadcast_deliveries_chat_idx", table_name="broadcast_deliveries")
    op.drop_index("broadcasts_status_idx", table_name="broadcasts")
    op.drop_index("jobs_ready_idx", table_name="jobs")
    op.drop_table("broadcast_deliveries")
    op.drop_table("broadcasts")
    op.drop_table("jobs")
    op.drop_table("subscriptions")
    op.drop_table("refunds")
    op.drop_table("payments")
    op.drop_table("users")
