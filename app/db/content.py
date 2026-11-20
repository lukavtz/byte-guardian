"""Content tables: categories, threats, localized texts, quizzes, runtime settings."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Category(Base):
    __tablename__ = "category"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    emoji: Mapped[str] = mapped_column(Text, nullable=False, server_default="•")
    titles: Mapped[dict[str, str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class Threat(Base):
    __tablename__ = "threat"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    category_slug: Mapped[str] = mapped_column(
        Text,
        ForeignKey("category.slug", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


class ThreatText(Base):
    __tablename__ = "threat_text"
    __table_args__ = (
        PrimaryKeyConstraint("threat_slug", "locale"),
    )

    threat_slug: Mapped[str] = mapped_column(
        Text, ForeignKey("threat.slug", ondelete="CASCADE"), nullable=False
    )
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    signs: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    before: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    during: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    after: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    forbidden: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    contacts: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


class QuizQuestion(Base):
    __tablename__ = "quiz_question"
    __table_args__ = (
        PrimaryKeyConstraint("threat_slug", "question_id", "locale"),
    )

    threat_slug: Mapped[str] = mapped_column(
        Text, ForeignKey("threat.slug", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str]] = mapped_column(postgresql.JSONB, nullable=False)
    answer: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


class QuizAnswer(Base):
    __tablename__ = "quiz_answer"
    __table_args__ = (
        UniqueConstraint(
            "threat_slug",
            "question_id",
            "session_seed",
            name="uq_quiz_answer_session",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    threat_slug: Mapped[str] = mapped_column(
        Text, ForeignKey("threat.slug", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_seed: Mapped[str] = mapped_column(Text, nullable=False)
    chosen: Mapped[int] = mapped_column(Integer, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )


class Settings(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False
    )