"""API key generation and hashing.

Format: `lg_live_<64 hex chars>` (128 bits of entropy). We hash the full
plaintext with SHA-256 for the DB lookup index; bcrypt would be wasted CPU
on every request since a 128-bit random value isn't brute-forceable offline.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "lg_live_"
PREFIX_DISPLAY_LEN = 12  # e.g. "lg_live_a3b7" — what the UI shows


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, display_prefix, sha256_hex)."""
    plaintext = KEY_PREFIX + secrets.token_hex(32)
    display_prefix = plaintext[:PREFIX_DISPLAY_LEN]
    return plaintext, display_prefix, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
