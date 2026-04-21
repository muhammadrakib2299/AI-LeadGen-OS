"""Unit tests for HMAC signing and delivery fan-out.

Full DB + HTTP tests would need Postgres; these focus on the deterministic
pieces so the signing contract is stable.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.services.webhooks import generate_secret, sign_payload


def test_sign_payload_matches_raw_hmac_sha256() -> None:
    secret = "top-secret"
    body = b'{"hello":"world"}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_payload(secret, body) == expected


def test_sign_payload_differs_on_different_secrets() -> None:
    body = b'{"a":1}'
    assert sign_payload("one", body) != sign_payload("two", body)


def test_sign_payload_is_stable_for_same_inputs() -> None:
    body = json.dumps({"event": "job.completed", "id": "x"}, sort_keys=True).encode()
    a = sign_payload("secret", body)
    b = sign_payload("secret", body)
    assert a == b


def test_generate_secret_is_high_entropy() -> None:
    seen = {generate_secret() for _ in range(100)}
    # 256 bits of randomness — practically zero collision chance.
    assert len(seen) == 100
    assert all(len(s) == 64 for s in seen)
