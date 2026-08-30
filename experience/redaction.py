from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization",
    "openai_api_key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "token",
}
BEARER_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)([\"']?)([^\s\"']+)(\2)"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)\b(OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|PASSWD|SECRET|TOKEN)"
    r"(\s*=\s*)([\"']?)([^\s\"']+)(\3)"
)


def redact_text(text: str | None) -> str | None:
    """Redact bounded secret-like forms before Experience persistence or hashing."""
    if text is None:
        return None
    redacted = BEARER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}{match.group(4)}",
        text,
    )
    return ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{REDACTED}{match.group(5)}"
        ),
        redacted,
    )


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Apply the single bounded Experience redaction policy recursively."""
    normalized_key = (key or "").strip().lower().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): redact_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
