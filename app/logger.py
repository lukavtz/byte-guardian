from __future__ import annotations

import base64
import logging
import re
import sys
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any
from urllib.parse import quote

import structlog

from app.runtime.secrets import read_secret

REDACTED = "***"
MIN_SECRET_LENGTH = 12

REDACTED_KEYS = frozenset({"bot_token", "webhook_secret", "llm_api_key", "authorization", "password"})

# Credentials that are secret whole; DSNs are secret only in their password part.
SECRET_NAMES = ("BOT_TOKEN", "LLM_API_KEY", "WEBHOOK_SECRET", "REDIS_PASSWORD")
URL_SECRET_NAMES = ("DATABASE_URL", "REDIS_URL")

_URL_PASSWORD = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?[^\s/@:]*:([^\s/@]+)@")

# Known credential shapes: short or ambiguous strings stay untouched so ordinary
# words such as "risk-assessment" or a bare "Bearer" are not rewritten.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{16,}=*"), r"\1" + REDACTED),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)"), r"\1" + REDACTED + r"\2"),
)

_EXCEPTION_FORMATTER = logging.Formatter()
# Credentials that are secret whole; DSNs are secret only in their password part.
SECRET_NAMES = ("BOT_TOKEN", "LLM_API_KEY", "WEBHOOK_SECRET", "REDIS_PASSWORD")
URL_SECRET_NAMES = ("DATABASE_URL", "REDIS_URL")

_URL_PASSWORD = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?[^\s/@:]*:([^\s/@]+)@")

# Known credential shapes: short or ambiguous strings stay untouched so ordinary
# words such as "risk-assessment" or a bare "Bearer" are not rewritten.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{16,}=*"), r"\1" + REDACTED),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)"), r"\1" + REDACTED + r"\2"),
)

_EXCEPTION_FORMATTER = logging.Formatter()


def _secret_forms(secret: str) -> set[str]:
    """Every shape a secret takes in logs: raw, multi-line, percent-encoded, base64."""
    value = secret.strip()
    if len(value) < MIN_SECRET_LENGTH:
        return set()
    forms = {value}
    forms.update(line.strip() for line in value.splitlines() if line.strip())
    forms.add(quote(value, safe=""))
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    forms.add(encoded)
    forms.add(encoded.rstrip("="))
    forms.add(base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("="))
    return forms


def redact_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """Replace known secret values and credential shapes with `***`."""
    forms = sorted(
        {form for secret in secrets for form in _secret_forms(secret)},
        key=len,
        reverse=True,
    )
    for form in forms:
        text = text.replace(form, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def load_secret_values(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Read the credentials a running instance holds, minus short or empty ones."""
    values: list[str] = []
    for name in SECRET_NAMES:
        value = read_secret(name, env)
        if value:
            values.append(value)
    for name in URL_SECRET_NAMES:
        value = read_secret(name, env)
        match = _URL_PASSWORD.match(value) if value else None
        if match:
            values.append(match.group(1))
    return tuple(value for value in values if len(value.strip()) >= MIN_SECRET_LENGTH)


class SecretRedactionFilter(logging.Filter):
    """Rewrite a record's message, args and traceback so a secret never reaches a handler."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # A traceback carries a library's own text, which is exactly where a token
        # or a connection string hides. Inline it redacted and clear the original,
        # so the formatter has nothing unredacted left to append.
        if record.exc_info is not None:
            message = f"{message}\n{_EXCEPTION_FORMATTER.formatException(record.exc_info)}"
            record.exc_info = None
        if record.stack_info is not None:
            message = f"{message}\n{record.stack_info}"
            record.stack_info = None
        record.exc_text = None
        record.msg = redact_secrets(message, self._secrets)
        record.args = ()
        return True


def _install(handler: logging.Handler, redaction: logging.Filter) -> None:
    handler.filters = [item for item in handler.filters if not isinstance(item, SecretRedactionFilter)]
    handler.addFilter(redaction)


def redact_processor(_logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = REDACTED
        elif isinstance(event_dict[key], dict):
            event_dict[key] = {
                nested: (REDACTED if nested.lower() in REDACTED_KEYS else value)
                for nested, value in event_dict[key].items()
            }
    return event_dict


def _redact_event_values(event_dict: MutableMapping[str, Any], secrets: tuple[str, ...]) -> MutableMapping[str, Any]:
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_secrets(value, secrets)
        elif isinstance(value, dict):
            event_dict[key] = {
                nested: (redact_secrets(nested_value, secrets) if isinstance(nested_value, str) else nested_value)
                for nested, nested_value in value.items()
            }
    return event_dict


def _value_redaction_processor(
    secrets: tuple[str, ...],
) -> Any:
    def processor(_logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        return _redact_event_values(redact_processor(_logger, _method_name, event_dict), secrets)

    return processor


def configure_logging(level: str = "info", secrets: Iterable[str] | None = None) -> None:
    resolved = load_secret_values() if secrets is None else tuple(secrets)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    redaction = SecretRedactionFilter(resolved)
    root = logging.getLogger()
    for handler in root.handlers:
        _install(handler, redaction)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            for handler in logger.handlers:
                _install(handler, redaction)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _value_redaction_processor(resolved),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
