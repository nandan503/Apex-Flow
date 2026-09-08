"""Sanitization layer for Render diagnostics artifacts.

Every byte that leaves the diagnostics collector — artifacts, stdout and the
GitHub Actions step summary — passes through this module.  The design is
deliberately layered; no single regex is trusted to catch everything:

1. Parsed JSON is walked and values under secret-looking keys are replaced
   before serialization.
2. Serialized text passes through an ordered set of pattern redactions:
   private key blocks, credential headers, bearer tokens, Render API keys,
   URLs with embedded userinfo, JWTs, cookie headers, common provider token
   prefixes and secret-looking ``key=value`` assignments.
3. The literal RENDER_API_TOKEN value is stripped from all output as a
   final guard, wherever it could have leaked into a response or error string.

Over-redaction is preferred to under-redaction.  Commit SHAs, request IDs
and similar infrastructure metadata deliberately survive; anything that
looks like a credential does not.

This module is stdlib-only and must remain importable without any
application dependency so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import re

REDACTED = "[REDACTED]"

# ---------------------------------------------------------------------------
# Layer 1: JSON key redaction
# ---------------------------------------------------------------------------

_SECRET_KEY_SEGMENTS = frozenset({
    "password", "passwd", "pwd", "passphrase", "secret", "secrets",
    "token", "tokens", "apikey", "key", "keys", "auth", "authorization",
    "cookie", "cookies", "session", "sessions", "credential", "credentials",
    "dsn", "private", "signature",
})

_KEY_CHUNK_RE = re.compile(r"[A-Za-z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _key_segments(key: str) -> list[str]:
    segments: list[str] = []
    for chunk in _KEY_CHUNK_RE.findall(key or ""):
        segments.extend(_CAMEL_SPLIT_RE.findall(chunk))
    return [s.lower() for s in segments]


def _key_is_secret(key: str) -> bool:
    return bool(_SECRET_KEY_SEGMENTS & set(_key_segments(key)))


def sanitize_json(obj: object) -> object:
    """Recursively redact secret-looking values in a parsed JSON object."""
    if isinstance(obj, dict):
        return {
            k: REDACTED if _key_is_secret(k) else sanitize_json(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_json(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Layer 2: Text pattern redaction
# ---------------------------------------------------------------------------

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL), REDACTED),
    # Authorization headers (Bearer/Basic/Token)
    (re.compile(r"(?i)(Authorization\s*:\s*(?:Bearer|Basic|Token)\s+)\S+"),
     r"\1" + REDACTED),
    # Render API key shape (rnd_*)
    (re.compile(r"\brnd_[A-Za-z0-9]{20,}\b"), REDACTED),
    # Generic Bearer token
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.~+/]+=*\b"), "Bearer " + REDACTED),
    # URLs with embedded userinfo (any scheme: https/postgresql/redis/mysql/etc.)
    (re.compile(r"[a-zA-Z][a-zA-Z0-9+\-.]*://[^@\s/]+:[^@\s/]+@"),
     "[SCHEME]://" + REDACTED + "@"),
    # JWTs (three base64url segments separated by dots)
    (re.compile(r"\bey[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b"),
     REDACTED),
    # Cookie header values
    (re.compile(r"(?i)(Cookie\s*:\s*).*"), r"\1" + REDACTED),
    (re.compile(r"(?i)(Set-Cookie\s*:\s*).*"), r"\1" + REDACTED),
    # Common provider token prefixes
    (re.compile(r"\b(sk|pk|ghp|github_pat|xoxb|xoxa|SG\.|AIza)[A-Za-z0-9\-_.]{10,}\b"),
     REDACTED),
    # key=value patterns with secret-looking keys
    (re.compile(
        r"(?i)\b(secret[_\-]?key|api[_\-]?key|password|passwd|token|auth[_\-]?token)"
        r"\s*[=:]\s*\S+",
        re.IGNORECASE),
     r"\1=[REDACTED]"),
]


def sanitize_text(text: str) -> str:
    """Apply all text-level redaction patterns to *text*."""
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_token(token: str) -> "TokenGuard":
    """Return a callable that strips the literal *token* value from text."""
    return TokenGuard(token)


class TokenGuard:
    """Strip the literal token value from any string (final safety net)."""

    def __init__(self, token: str) -> None:
        self._token = token or ""

    def __call__(self, text: str = "") -> str:
        if not self._token or not text:
            return text
        return text.replace(self._token, REDACTED)
