"""Tests for the Google Sheets export service.

Generates a throwaway RSA key per test session to sign real JWTs — the
service module uses PyJWT under the hood, so anything less wouldn't
exercise the actual sign path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.export import CSV_COLUMNS
from app.services.google_sheets import (
    SHEETS_API,
    SHEETS_SCOPE,
    append_entities,
    build_jwt_assertion,
    entity_to_row,
    header_row,
    parse_service_account,
)


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture
def sa_blob(rsa_keypair: tuple[str, str]) -> str:
    private_pem, _ = rsa_keypair
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "exporter@proj.iam.gserviceaccount.com",
            "private_key": private_pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@dataclass
class _FakeEntity:
    name: str = ""
    website: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    country: str = ""
    category: str = ""
    quality_score: int | None = None
    review_status: str = "pending"
    socials: dict | None = None
    field_sources: dict | None = None
    external_ids: dict | None = None


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_service_account("not-json")


def test_parse_rejects_missing_field(rsa_keypair: tuple[str, str]) -> None:
    private_pem, _ = rsa_keypair
    blob = json.dumps({"client_email": "x@y", "private_key": private_pem})
    with pytest.raises(ValueError, match="token_uri"):
        parse_service_account(blob)


def test_parse_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_service_account('["not", "an", "object"]')


def test_jwt_assertion_has_required_claims(
    sa_blob: str, rsa_keypair: tuple[str, str]
) -> None:
    creds = parse_service_account(sa_blob)
    _, public_pem = rsa_keypair
    token = build_jwt_assertion(creds, now=1_700_000_000)
    decoded = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        audience=creds["token_uri"],
        options={"verify_exp": False},
    )
    assert decoded["iss"] == creds["client_email"]
    assert decoded["scope"] == SHEETS_SCOPE
    assert decoded["aud"] == creds["token_uri"]
    assert decoded["iat"] == 1_700_000_000
    assert decoded["exp"] == 1_700_000_000 + 3600


def test_entity_to_row_matches_csv_columns_in_order() -> None:
    e = _FakeEntity(
        name="Acme",
        email="hi@acme.example",
        website="https://acme.example",
        city="Paris",
        country="FR",
        quality_score=87,
        socials={"linkedin": "acme"},
        external_ids={"google_place_id": "P1"},
        field_sources={"email": {"source": "crawler"}},
    )
    row = entity_to_row(e)
    assert len(row) == len(CSV_COLUMNS)
    by_col = dict(zip(CSV_COLUMNS, row, strict=True))
    assert by_col["name"] == "Acme"
    assert by_col["email"] == "hi@acme.example"
    assert by_col["quality_score"] == "87"
    assert by_col["socials_linkedin"] == "acme"
    assert by_col["google_place_id"] == "P1"
    assert by_col["email_source"] == "crawler"


def test_header_row_matches_csv_columns() -> None:
    assert header_row() == list(CSV_COLUMNS)


async def test_append_entities_noop_on_empty(sa_blob: str) -> None:
    result = await append_entities(sa_blob, "sheet-id", "Leads", [])
    assert result.appended == 0
    assert result.errors == []


@respx.mock
async def test_append_entities_happy_path(sa_blob: str) -> None:
    creds = parse_service_account(sa_blob)
    token_route = respx.post(creds["token_uri"]).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.test", "expires_in": 3600}
        )
    )
    append_url = f"{SHEETS_API}/sheet-1/values/Leads!A:Z:append"
    append_route = respx.post(append_url).mock(
        return_value=httpx.Response(
            200, json={"updates": {"updatedRows": 2}}
        )
    )
    entities = [
        _FakeEntity(name="A", email="a@x.example"),
        _FakeEntity(name="B", email="b@x.example"),
    ]
    async with httpx.AsyncClient() as http:
        result = await append_entities(
            sa_blob, "sheet-1", "Leads", entities, http=http
        )
    assert result.appended == 2
    assert result.errors == []
    assert token_route.call_count == 1
    assert append_route.call_count == 1
    append_req = append_route.calls.last.request
    assert append_req.headers["Authorization"] == "Bearer ya29.test"
    assert append_req.url.params["valueInputOption"] == "RAW"
    body = json.loads(append_req.content)
    assert len(body["values"]) == 2


@respx.mock
async def test_append_entities_includes_header_when_requested(sa_blob: str) -> None:
    creds = parse_service_account(sa_blob)
    respx.post(creds["token_uri"]).mock(
        return_value=httpx.Response(200, json={"access_token": "tkn"})
    )
    append_url = f"{SHEETS_API}/sheet-1/values/Leads!A:Z:append"
    append_route = respx.post(append_url).mock(
        return_value=httpx.Response(200, json={"updates": {"updatedRows": 2}})
    )
    async with httpx.AsyncClient() as http:
        await append_entities(
            sa_blob,
            "sheet-1",
            "Leads",
            [_FakeEntity(name="A", email="a@x.example")],
            include_header=True,
            http=http,
        )
    body = json.loads(append_route.calls.last.request.content)
    assert body["values"][0] == list(CSV_COLUMNS)
    assert len(body["values"]) == 2  # header + 1 row


@respx.mock
async def test_append_entities_returns_token_error(sa_blob: str) -> None:
    creds = parse_service_account(sa_blob)
    respx.post(creds["token_uri"]).mock(
        return_value=httpx.Response(401, text="invalid_grant")
    )
    async with httpx.AsyncClient() as http:
        result = await append_entities(
            sa_blob,
            "sheet-1",
            "Leads",
            [_FakeEntity(name="A", email="a@x.example")],
            http=http,
        )
    assert result.appended == 0
    assert result.errors and "token" in result.errors[0]


@respx.mock
async def test_append_entities_returns_sheets_error(sa_blob: str) -> None:
    creds = parse_service_account(sa_blob)
    respx.post(creds["token_uri"]).mock(
        return_value=httpx.Response(200, json={"access_token": "tkn"})
    )
    append_url = f"{SHEETS_API}/sheet-1/values/Leads!A:Z:append"
    respx.post(append_url).mock(
        return_value=httpx.Response(403, text="caller does not have permission")
    )
    async with httpx.AsyncClient() as http:
        result = await append_entities(
            sa_blob,
            "sheet-1",
            "Leads",
            [_FakeEntity(name="A", email="a@x.example")],
            http=http,
        )
    assert result.appended == 0
    assert result.errors and "403" in result.errors[0]
