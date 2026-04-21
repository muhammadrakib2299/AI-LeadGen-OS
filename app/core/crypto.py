"""Fernet-backed helpers for column-level PII encryption.

Threat model: protect PII in DB backups and read replicas, where disk
encryption may not apply. Not a defense against a compromised app server —
the key lives there.

On-disk format: every encrypted value is prefixed with `enc:v1:` so that:
- Reads can transparently pass plaintext through when migrating an existing
  column (old rows start as plaintext, new rows land encrypted).
- The version prefix leaves a clean upgrade path to `enc:v2:` (e.g. a new
  AEAD construction) without a migration.

Use via the SQLAlchemy TypeDecorator in `app/db/types.py`; direct calls are
fine for ad-hoc scripts.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_PREFIX = "enc:v1:"


class EncryptionNotConfiguredError(RuntimeError):
    """Raised when a write is attempted but APP_ENCRYPTION_KEY is unset."""


class EncryptionKeyInvalidError(RuntimeError):
    """Raised when the configured key can't decrypt a stored ciphertext."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet | None:
    settings = get_settings()
    if not settings.app_encryption_key:
        return None
    try:
        return Fernet(settings.app_encryption_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise EncryptionNotConfiguredError(
            "APP_ENCRYPTION_KEY is set but not a valid Fernet key "
            "(expected 32 url-safe base64 bytes)"
        ) from exc


def encrypt_str(value: str) -> str:
    """Encrypt `value`; raise if no key is configured.

    Writers should treat a missing key as a configuration error so that an
    operator doesn't silently store plaintext in a column they thought was
    encrypted. Readers are more forgiving — see `decrypt_str`.
    """
    f = _fernet()
    if f is None:
        raise EncryptionNotConfiguredError(
            "APP_ENCRYPTION_KEY is not set — cannot encrypt PII columns"
        )
    token = f.encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_str(value: str) -> str:
    """Decrypt `value`. Passes through plaintext (no `enc:v1:` prefix).

    The pass-through keeps us compatible with rows written before encryption
    was turned on and with dev DBs that haven't set the key. A rotation
    strategy that retires the passthrough lives in a future v2.
    """
    if not value.startswith(_PREFIX):
        return value
    f = _fernet()
    if f is None:
        raise EncryptionNotConfiguredError(
            "Found encrypted value but APP_ENCRYPTION_KEY is not set"
        )
    token = value[len(_PREFIX) :].encode("ascii")
    try:
        return f.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionKeyInvalidError(
            "APP_ENCRYPTION_KEY does not match the key used for this value"
        ) from exc


def reset_cache_for_tests() -> None:
    """Test hook: forget the cached Fernet so a new key picks up."""
    _fernet.cache_clear()
