"""Google Sheets export via service-account auth.

The tenant pastes a Google Cloud service-account JSON key blob into the
app, then shares their target spreadsheet with the service account's
client_email as Editor. This is the simplest "paste credentials" UX —
OAuth would need a redirect dance we don't want for a pasteable secret.

Auth flow (no google-auth dependency — PyJWT + cryptography are already
in deps and that's all we need):
1. Sign a short-lived JWT with the SA private key (RS256).
2. Exchange it at the token endpoint for a 1h Bearer access token.
3. POST rows to spreadsheets.values:append.

Column layout matches `app/services/export.CSV_COLUMNS` so a Sheets
export and a CSV export of the same job have identical columns.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.core.logging import get_logger
from app.services.export import CSV_COLUMNS, _row

log = get_logger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
JWT_LIFETIME_SECONDS = 3600


@dataclass(slots=True)
class SheetsAppendResult:
    appended: int
    errors: list[str]


def parse_service_account(blob: str) -> dict[str, Any]:
    """Validate the SA JSON blob and return it as a dict.

    Raises ValueError on missing required fields so the API layer can
    return a clean 400 instead of crashing inside the JWT signer.
    """
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f"service account JSON is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("service account JSON must be a JSON object")
    for required in ("client_email", "private_key", "token_uri"):
        if not data.get(required):
            raise ValueError(f"service account JSON missing field: {required}")
    return data


def build_jwt_assertion(creds: dict[str, Any], *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "iss": creds["client_email"],
        "scope": SHEETS_SCOPE,
        "aud": creds["token_uri"],
        "iat": issued_at,
        "exp": issued_at + JWT_LIFETIME_SECONDS,
    }
    return jwt.encode(payload, creds["private_key"], algorithm="RS256")


async def fetch_access_token(
    creds: dict[str, Any], *, http: httpx.AsyncClient
) -> str:
    """Exchange a signed JWT for a Google OAuth2 access token."""
    assertion = build_jwt_assertion(creds)
    response = await http.post(
        creds["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"google token exchange failed {response.status_code}: "
            f"{response.text[:200]}"
        )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("google token response missing access_token")
    return token


def entity_to_row(entity: Any) -> list[str]:
    """Serialize an entity to a row matching CSV_COLUMNS order."""
    row_dict = _row(entity)
    return [row_dict[col] for col in CSV_COLUMNS]


def header_row() -> list[str]:
    return list(CSV_COLUMNS)


async def append_entities(
    service_account_json: str,
    spreadsheet_id: str,
    worksheet_name: str,
    entities: list[Any],
    *,
    include_header: bool = False,
    http: httpx.AsyncClient | None = None,
) -> SheetsAppendResult:
    """Append rows to the given worksheet. Caller decides whether to send a header row."""
    if not entities:
        return SheetsAppendResult(appended=0, errors=[])

    creds = parse_service_account(service_account_json)
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        try:
            token = await fetch_access_token(creds, http=client)
        except (httpx.HTTPError, RuntimeError) as exc:
            return SheetsAppendResult(appended=0, errors=[f"token: {exc}"])

        rows = [entity_to_row(e) for e in entities]
        if include_header:
            rows = [header_row(), *rows]

        # A1 range covering all CSV columns; Sheets autoexpands rows.
        range_a1 = f"{worksheet_name}!A:Z"
        url = f"{SHEETS_API}/{spreadsheet_id}/values/{range_a1}:append"
        try:
            response = await client.post(
                url,
                params={
                    "valueInputOption": "RAW",
                    "insertDataOption": "INSERT_ROWS",
                },
                headers={"Authorization": f"Bearer {token}"},
                json={"values": rows},
            )
        except httpx.HTTPError as exc:
            return SheetsAppendResult(
                appended=0, errors=[f"{type(exc).__name__}: {exc}"]
            )

        if response.status_code >= 400:
            return SheetsAppendResult(
                appended=0,
                errors=[f"sheets {response.status_code}: {response.text[:200]}"],
            )
        parsed = response.json()
        # Sheets returns updates.updatedRows on success. Fall back to len(rows)
        # so include_header doesn't make us under-report by 1 if absent.
        updated = (parsed.get("updates") or {}).get("updatedRows")
        appended = int(updated) if updated is not None else len(rows)
        return SheetsAppendResult(appended=appended, errors=[])
    finally:
        if owns_http:
            await client.aclose()
