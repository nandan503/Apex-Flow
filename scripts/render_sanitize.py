"""Sanitization layer for Render diagnostics artifacts.

Every byte that leaves the diagnostics collector -- artifacts, stdout and the
GitHub Actions step summary -- passes through this module. The design is
layered on purpose; no single regex is trusted to catch everything:

1. Parsed JSON is walked and values under secret-looking keys are replaced
   before serialization (``registryCredential``, ``api_key`` ...).
2. Serialized text passes through an ordered set of pattern redactions:
   private key blocks, credential headers, bearer tokens, Render API keys,
   URLs with embedded userinfo, JWTs, cookie headers, common provider token
   prefixes and secret-looking ``key=value`` assignments.
3. The literal RENDER_API_TOKEN value is stripped from all output as a final
   guard, wherever it could have leaked into a response or error string.

Over-redaction is preferred to under-redaction. Commit SHAs, request IDs and
similar infrastructure metadata deliberately survive; anything that looks
like a credential does not. Bare high-entropy strings are intentionally NOT
redacted by a generic entropy rule, because that would destroy commit SHAs
and resource IDs that the diagnostics depend on; instead each pattern is
anchored on a credential shape.

This module is stdlib-only and must stay importable without any application
dependency so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import re

REDACTED = '[REDACTED]'

# ---------------------------------------------------------------------------
# Layer 1: JSON key redaction
# ---------------------------------------------------------------------------

# Key segments (splitting on snake_case / kebab-case / camelCase boundaries)
# that mark a value as secret. "registryCredential" -> {"registry",
# "credential"} -> redacted; "healthCheckPath" -> no match -> preserved.
_SECRET_KEY_SEGMENTS = frozenset({
    'password', 'passwd', 'pwd', 'passphrase', 'secret', 'secrets', 'token',
    'tokens', 'apikey', 'key', 'keys', 'auth', 'authorization', 'cookie',
    'cookies', 'session', 'sessions', 'credential', 'credentials', 'dsn',
    'private', 'signature',
})

_KEY_CHUNK_RE = re.compile(r'[A-Za-z0-9]+')
_CAMEL_SPLIT_RE = re.compile(r'[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])')


def _key_segments(key):
    """Split a key on snake/kebab/camelCase boundaries, lowercased."""
    segments = []
    for chunk in _KEY_CHUNK_RE.findall(key or ''):
        segments.extend(_CAMEL_SPLIT_RE.findall(chunk))
    return [segment.lower() for segment in segments]


def _key_is_secret(key):
    return any(seg in _SECRET_KEY_SEGMENTS for seg in _key_segments(key))


def redact_json(value):
    """Return a deep copy of parsed JSON with secret-keyed values redacted."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _key_is_secret(k) else redact_json(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


# ---------------------------------------------------------------------------
# Layer 2: text pattern redaction (ordered)
# ---------------------------------------------------------------------------

_PRIVATE_KEY_RE = re.compile(
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----')

_CREDENTIAL_HEADER_RE = re.compile(
    r'(?i)\b(authorization|proxy-authorization|x-auth-token|x-api-key|api-key)'
    r'(\s*[:=]\s*)[^\r\n]+')

_BEARER_RE = re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}')

# Userinfo embedded in a URL: scheme://user:password@host or scheme://user@host
# (covers postgres://, postgresql://, redis://, rediss://, amqp://, https://...).
_URL_USERINFO_RE = re.compile(r'\b[a-z][a-z0-9+.\-]*://[^\s/@:]+(?::[^\s/@]*)?@')

_JWT_RE = re.compile(r'\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\b')

_COOKIE_RE = re.compile(r'(?i)\b(set-)?cookie(\s*[:=]\s*)[^\r\n]+')

_TOKEN_PREFIX_RE = re.compile(
    r'\b(?:'
    r'rnd_[A-Za-z0-9_-]{8,}'               # Render API key
    r'|rmt_[A-Za-z0-9_-]{8,}'              # Render machine/CLI token
    r'|gh[pousr]_[A-Za-z0-9]{20,}'         # GitHub tokens
    r'|github_pat_[A-Za-z0-9_]{20,}'       # GitHub fine-grained PAT
    r'|xox[baprs]-[A-Za-z0-9-]{10,}'       # Slack tokens
    r'|AKIA[0-9A-Z]{16}'                   # AWS access key ID
    r'|sk-(?:live|test)-[A-Za-z0-9]{16,}'  # Stripe secret keys
    r'|AIza[0-9A-Za-z_-]{30,}'             # Google API keys
    r')\b')

# Secret-looking key/value assignment in log text, shell output, env dumps or
# serialized JSON: password=hunter2, "token": "abc123", session => xyz ...
_KV_SECRET_RE = re.compile(
    r'(?i)(?<![\w-])('
    r'password|passwd|pwd|passphrase|secret|secrets|token|tokens'
    r'|api[_-]?key|apikey|access[_-]?key'
    r'|auth|credential|credentials|private[_-]?key'
    r'|session|sessions|dsn'
    r')(?![\w-])(["\']?[ \t]*(?:[:=]|=>)[ \t]*)(["\']?)[^\s"\',;&}]{3,}')


def _redact_credential_header(match):
    return f'{match.group(1)}{match.group(2)}{REDACTED}'


def _redact_bearer(match):
    return f'bearer {REDACTED}'


def _redact_url_userinfo(match):
    scheme = match.group(0).split('://', 1)[0]
    return f'{scheme}://{REDACTED}@'


def _redact_cookie(match):
    return f'{match.group(1) or ""}cookie{match.group(2)}{REDACTED}'


def _redact_kv(match):
    return f'{match.group(1)}{match.group(2)}{match.group(3)}{REDACTED}'


def sanitize_text(text):
    """Apply ordered pattern redactions to a piece of text."""
    if not isinstance(text, str) or not text:
        return text or ''
    text = _PRIVATE_KEY_RE.sub(f'{REDACTED} private key', text)
    text = _CREDENTIAL_HEADER_RE.sub(_redact_credential_header, text)
    text = _BEARER_RE.sub(_redact_bearer, text)
    text = _JWT_RE.sub(f'{REDACTED} jwt', text)
    text = _COOKIE_RE.sub(_redact_cookie, text)
    text = _URL_USERINFO_RE.sub(_redact_url_userinfo, text)
    text = _TOKEN_PREFIX_RE.sub(REDACTED, text)
    text = _KV_SECRET_RE.sub(_redact_kv, text)
    return text


# ---------------------------------------------------------------------------
# Layer 3: literal token guard
# ---------------------------------------------------------------------------


def sanitize_token(text, token):
    """Remove the literal secret value (e.g. RENDER_API_TOKEN) from text."""
    if text and token and len(token) >= 8:
        return text.replace(token, REDACTED)
    return text


# ---------------------------------------------------------------------------
# Combined helpers
# ---------------------------------------------------------------------------


def sanitize_json(value, token=None):
    """Redact a JSON-serializable structure and return sanitized JSON text."""
    text = json.dumps(redact_json(value), indent=2, ensure_ascii=False,
                      default=str)
    text = sanitize_text(text)
    if token:
        text = sanitize_token(text, token)
    return text
