"""admin edits — edited_at markers and a guard that survives reseeding.

Revision ID: 0007_admin_edits
Revises: 0006_user_locale_chosen
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_admin_edits"
down_revision = "0006_user_locale_chosen"
branch_labels = None
depends_on = None

EDITED_TABLES = ("category", "threat", "threat_text", "quiz_question")
VERSIONED_TABLES = ("threat_text", "quiz_question")


def upgrade() -> None:
    for table in EDITED_TABLES:
        op.add_column(
            table, sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
        )
    for table in VERSIONED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "admin_version", sa.Integer(), nullable=False, server_default="0"
            ),
        )


def downgrade() -> None:
    for table in VERSIONED_TABLES:
        op.drop_column(table, "admin_version")
    for table in EDITED_TABLES:
        op.drop_column(table, "edited_at")