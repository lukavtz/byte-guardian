"""users.locale_chosen — a language the user picked is never re-pinned.

Revision ID: 0006_user_locale_chosen
Revises: 0005_broadcast_total
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_user_locale_chosen"
down_revision = "0005_broadcast_total"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "locale_chosen", sa.Boolean(), nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "locale_chosen")