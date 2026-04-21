"""GET /settings — read-only view of compliance-relevant toggles.

Deliberately narrow: returns only flags the UI needs to render correctly.
Sensitive config (API keys, DB URL, JWT secret) never leaves this process.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class ComplianceSettingsResponse(BaseModel):
    compliant_mode: bool
    jurisdiction: str


@router.get("/compliance", response_model=ComplianceSettingsResponse)
async def get_compliance() -> ComplianceSettingsResponse:
    s = get_settings()
    return ComplianceSettingsResponse(
        compliant_mode=s.compliant_mode,
        jurisdiction=s.jurisdiction,
    )
