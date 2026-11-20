"""content schema

Revision ID: 0002_content
Revises: 0001_runtime_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_content"
down_revision = "0001_runtime_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "broadcasts_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.create_table(
        "category",
        sa.Column("slug", sa.Text(), primary_key=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emoji", sa.Text(), nullable=False, server_default="•"),
        sa.Column(
            "titles",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
    )
    op.create_table(
        "threat",
        sa.Column("slug", sa.Text(), primary_key=True),
        sa.Column(
            "category_slug",
            sa.Text(),
            sa.ForeignKey("category.slug", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "content_version", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_threat_category_slug", "threat", ["category_slug"])
    op.create_table(
        "threat_text",
        sa.Column(
            "threat_slug",
            sa.Text(),
            sa.ForeignKey("threat.slug", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("locale", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("signs", postgresql.JSONB(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("during", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("forbidden", postgresql.JSONB(), nullable=False),
        sa.Column("contacts", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "content_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.PrimaryKeyConstraint("threat_slug", "locale"),
    )
    op.create_table(
        "quiz_question",
        sa.Column(
            "threat_slug",
            sa.Text(),
            sa.ForeignKey("threat.slug", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("answer", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "content_version", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.PrimaryKeyConstraint("threat_slug", "question_id", "locale"),
    )
    op.create_table(
        "quiz_answer",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "threat_slug",
            sa.Text(),
            sa.ForeignKey("threat.slug", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("session_seed", sa.Text(), nullable=False),
        sa.Column("chosen", sa.Integer(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_quiz_answer_telegram_id", "quiz_answer", ["telegram_id"]
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_quiz_answer_telegram_id", table_name="quiz_answer")
    op.drop_table("quiz_answer")
    op.drop_table("quiz_question")
    op.drop_table("threat_text")
    op.drop_index("ix_threat_category_slug", table_name="threat")
    op.drop_table("threat")
    op.drop_table("category")
    op.drop_column("users", "broadcasts_opt_in")