"""users.locale — the chosen interface language.

Revision ID: 0004_user_locale
Revises: 0003_quiz_answer_unique
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_user_locale"
down_revision = "0003_quiz_answer_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locale", sa.Text(), nullable=False, server_default="ru"),
    )


def downgrade() -> None:
    op.drop_column("users", "locale")