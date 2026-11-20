"""card enrichment — nullable fields for quick guidance and citations.

Revision ID: 0008_card_enrichment
Revises: 0007_admin_edits
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_card_enrichment"
down_revision = "0007_admin_edits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threat", sa.Column("updated", sa.Date(), nullable=True))
    op.add_column("threat_text", sa.Column("quick", sa.Text(), nullable=True))
    op.add_column(
        "threat_text",
        sa.Column("mistakes", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "threat_text",
        sa.Column("citations", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("threat_text", "citations")
    op.drop_column("threat_text", "mistakes")
    op.drop_column("threat_text", "quick")
    op.drop_column("threat", "updated")
