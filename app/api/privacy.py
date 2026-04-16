"""Privacy endpoints — opt-out (GDPR Art. 17/21). Stub for Phase 0.

Proper implementation with DB-backed blacklist table ships in Phase 1.
"""

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.core.logging import get_logger

router = APIRouter(prefix="/privacy", tags=["privacy"])
log = get_logger(__name__)


class OptOutRequest(BaseModel):
    email: EmailStr
    reason: str | None = None


class OptOutResponse(BaseModel):
    status: str
    message: str


@router.post("/opt-out", response_model=OptOutResponse)
async def opt_out(payload: OptOutRequest) -> OptOutResponse:
    log.info(
        "opt_out_received",
        email_domain=payload.email.split("@")[1],
        has_reason=payload.reason is not None,
    )
    return OptOutResponse(
        status="accepted",
        message="Your request has been recorded. Processing within 72 hours.",
    )
