"""Tests for app.core.crypto — PII encryption helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.crypto import (
    EncryptionKeyInvalidError,
    EncryptionNotConfiguredError,
    decrypt_str,
    encrypt_str,
)


@pytest.fixture
def with_key():
    key = Fernet.generate_key().decode()
    with patch("app.core.crypto.get_settings") as mock:
        mock.return_value.app_encryption_key = key
        crypto.reset_cache_for_tests()
        yield key
    crypto.reset_cache_for_tests()


@pytest.fixture
def without_key():
    with patch("app.core.crypto.get_settings") as mock:
        mock.return_value.app_encryption_key = None
        crypto.reset_cache_for_tests()
        yield
    crypto.reset_cache_for_tests()


def test_encrypt_decrypt_roundtrip(with_key: str) -> None:
    ct = encrypt_str("+44 20 1234 5678")
    assert ct.startswith("enc:v1:")
    assert decrypt_str(ct) == "+44 20 1234 5678"


def test_encrypt_produces_different_ciphertexts_for_same_plaintext(
    with_key: str,
) -> None:
    # Fernet includes an IV, so two encrypts of the same plaintext differ.
    a = encrypt_str("same input")
    b = encrypt_str("same input")
    assert a != b
    assert decrypt_str(a) == decrypt_str(b) == "same input"


def test_decrypt_passthrough_for_plaintext(with_key: str) -> None:
    # A row written before encryption was turned on must still be readable.
    assert decrypt_str("+44 20 1234 5678") == "+44 20 1234 5678"


def test_encrypt_without_key_raises(without_key) -> None:
    with pytest.raises(EncryptionNotConfiguredError):
        encrypt_str("sensitive")


def test_decrypt_plaintext_without_key_is_ok(without_key) -> None:
    # Reads of legacy plaintext are fine without a key.
    assert decrypt_str("plain text") == "plain text"


def test_decrypt_encrypted_without_key_raises(without_key) -> None:
    # But if we find ciphertext, refusing is the right move.
    with pytest.raises(EncryptionNotConfiguredError):
        decrypt_str("enc:v1:gAAAAA" + "x" * 50)


def test_decrypt_with_wrong_key_raises() -> None:
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    with patch("app.core.crypto.get_settings") as mock:
        mock.return_value.app_encryption_key = key_a
        crypto.reset_cache_for_tests()
        ct = encrypt_str("secret")

    with patch("app.core.crypto.get_settings") as mock:
        mock.return_value.app_encryption_key = key_b
        crypto.reset_cache_for_tests()
        with pytest.raises(EncryptionKeyInvalidError):
            decrypt_str(ct)
    crypto.reset_cache_for_tests()
