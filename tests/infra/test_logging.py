from __future__ import annotations

import io
import logging
import sys
from collections.abc import Callable

import pytest

from app.logger import (
    SecretRedactionFilter,
    configure_logging,
    load_secret_values,
    redact_secrets,
)

TOKEN = "123456:" + "A" * 30
LLM_KEY = "sk-" + "b" * 24
DB_PASSWORD = "db-password-123456"
DSN = f"postgres://bot_user:{DB_PASSWORD}@postgres:5432/bot"


def run_logger(action: Callable[[logging.Logger], None], secrets: tuple[str, ...] = ()) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(SecretRedactionFilter(secrets))
    logger = logging.getLogger(f"test.secret.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    action(logger)
    handler.flush()
    return stream.getvalue()


def assert_redacted(output: str, secret: str) -> None:
    assert secret not in output
    assert "***" in output


def test_redacts_token_inside_message_text() -> None:
    output = run_logger(lambda log: log.error("telegram auth failed for %s", TOKEN))
    assert_redacted(output, TOKEN)


def test_redacts_token_passed_as_message_argument() -> None:
    output = run_logger(lambda log: log.error("connect with %r", {"token": TOKEN}))
    assert_redacted(output, TOKEN)


def test_redacts_token_inside_exception_traceback() -> None:
    def fail() -> None:
        raise RuntimeError(f"upstream rejected {TOKEN}")

    def action(log: logging.Logger) -> None:
        try:
            fail()
        except RuntimeError:
            log.exception("handshake failed")

    output = run_logger(action)
    assert_redacted(output, TOKEN)
    assert "RuntimeError" in output


def test_redacts_password_in_database_url() -> None:
    output = run_logger(lambda log: log.error("cannot connect to %s", DSN))
    assert DB_PASSWORD not in output
    assert "***" in output
    assert "bot_user" in output
    assert "postgres:5432" in output


def test_redacts_known_secret_value_anywhere() -> None:
    secret = "custom-secret-value-9876"
    output = run_logger(lambda log: log.warning("payload echoed %s back", secret), secrets=(secret,))
    assert_redacted(output, secret)


def test_redacts_api_key_and_bearer_header() -> None:
    header = "Authorization: Bearer abcdefghijklmnop123456"
    output = run_logger(lambda log: log.error("request failed: %s key=%s", header, LLM_KEY))
    assert_redacted(output, LLM_KEY)
    assert "abcdefghijklmnop123456" not in output
    assert "Bearer " in output


def test_leaves_short_and_ordinary_values_alone() -> None:
    output = run_logger(
        lambda log: log.info("risk-assessment finished; Bearer is just a word"),
        secrets=("sk-test", "short"),
    )
    assert "risk-assessment" in output
    assert "Bearer is just a word" in output


def test_filter_rewrites_record_fields() -> None:
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "token %s", (TOKEN,), None)
    assert SecretRedactionFilter().filter(record) is True
    assert record.args == ()
    assert isinstance(record.msg, str)
    assert TOKEN not in record.msg
    assert "***" in record.msg


def test_filter_clears_exception_after_inlining_it() -> None:
    try:
        raise ValueError(f"leaked {TOKEN}")
    except ValueError:
        record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert SecretRedactionFilter().filter(record) is True
    assert record.exc_info is None
    assert record.exc_text is None
    assert TOKEN not in record.getMessage()
    assert "***" in record.getMessage()


def test_load_secret_values_skips_short_and_extracts_database_password() -> None:
    env = {
        "BOT_TOKEN": TOKEN,
        "LLM_API_KEY": "sk-test",
        "WEBHOOK_SECRET": "short",
        "DATABASE_URL": DSN,
        "REDIS_PASSWORD": "redis-password-123",
    }
    values = set(load_secret_values(env))
    assert TOKEN in values
    assert DB_PASSWORD in values
    assert "redis-password-123" in values
    assert "sk-test" not in values
    assert "short" not in values
    assert DSN not in values


def test_redact_secrets_is_idempotent() -> None:
    once = redact_secrets(f"a {TOKEN} b", ())
    twice = redact_secrets(once, (TOKEN,))
    assert once == twice
    assert TOKEN not in twice


def test_configure_logging_installs_filter_on_root_handlers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    handler = logging.StreamHandler(sys.stdout)
    root.addHandler(handler)
    try:
        configure_logging("info", secrets=(TOKEN,))
        assert any(isinstance(item, SecretRedactionFilter) for item in handler.filters)
        logging.getLogger("app.test").error("token is %s", TOKEN)
        output = capsys.readouterr().out
        assert TOKEN not in output
        assert "***" in output
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)
