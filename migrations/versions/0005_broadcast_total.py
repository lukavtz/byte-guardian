"""broadcasts.total — the audience size a job reports progress against.

Revision ID: 0005_broadcast_total
Revises: 0004_user_locale
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_broadcast_total"
down_revision = "0004_user_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broadcasts",
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("broadcasts", "total")