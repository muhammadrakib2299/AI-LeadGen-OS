"""Custom SQLAlchemy column types.

`EncryptedString` transparently encrypts on write and decrypts on read using
`app.core.crypto`. Intended for PII columns where disk encryption alone isn't
sufficient (backups, replicas). See compliance.md §8.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.crypto import decrypt_str, encrypt_str


class EncryptedString(TypeDecorator[str]):
    """Text column whose contents are encrypted on the DB side of the wire.

    Pass-through for None; otherwise delegates to `encrypt_str` / `decrypt_str`.
    The underlying column is stored as a plain String — ciphertext is
    base64-ish ASCII and fits without needing a BYTEA column type.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        return encrypt_str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        return decrypt_str(value)
