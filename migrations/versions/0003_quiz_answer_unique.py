"""one answer per quiz session question

Makes ``(threat_slug, question_id, session_seed)`` unique so a failed
``INSERT … ON CONFLICT`` target cannot raise 42P10, and so a session question
belongs to exactly one user.

Revision ID: 0003_quiz_answer_unique
Revises: 0002_content
"""

from alembic import op

revision = "0003_quiz_answer_unique"
down_revision = "0002_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_quiz_answer_session",
        "quiz_answer",
        ["threat_slug", "question_id", "session_seed"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_quiz_answer_session", "quiz_answer", type_="unique")