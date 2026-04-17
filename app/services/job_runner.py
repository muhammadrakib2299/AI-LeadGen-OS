"""End-to-end Phase 1 pipeline: validate -> Places -> crawl -> extract -> persist.

Runs inline (no Celery yet) — Phase 2 wraps this with BullMQ-equivalent workers.
Keeps all dependencies injected so tests can swap in fakes without touching real APIs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity, Job
from app.extractors.contacts import ContactsExtractor
from app.models.contacts import ExtractedContacts
from app.models.places import Place
from app.models.query import QueryRequest, QueryValidationError, ValidatedQuery
from app.services.blacklist import is_blacklisted
from app.services.crawler import Crawler, CrawlResult
from app.services.dedupe import dedupe_job
from app.services.email_verify import EmailVerification, verify_email
from app.services.phone_verify import PhoneVerification, verify_phone
from app.services.places import TEXT_SEARCH_COST_USD, PlacesClient
from app.services.quality import review_status_for, score_entity
from app.services.query_validator import QueryValidator
from app.services.url_liveness import UrlLiveness, liveness_from_crawl

log = get_logger(__name__)


class BudgetExceededError(Exception):
    pass


class JobRunner:
    def __init__(
        self,
        *,
        validator: QueryValidator,
        places: PlacesClient,
        crawler: Crawler,
        extractor: ContactsExtractor,
        session: AsyncSession,
        verify_emails: bool = True,
    ) -> None:
        self._validator = validator
        self._places = places
        self._crawler = crawler
        self._extractor = extractor
        self._session = session
        self._verify_emails = verify_emails

    async def run(self, job: Job) -> Job:
        job.status = "running"
        job.started_at = _now()
        await self._session.flush()

        try:
            validated = await self._validate(job)
            if validated is None:
                return job

            cost_tracker = _CostTracker(cap=float(job.budget_cap_usd))

            try:
                places = await self._discover(job, validated, cost_tracker)
            except BudgetExceededError:
                log.warning("job_budget_exceeded_on_discovery", job_id=str(job.id))
                job.status = "budget_exceeded"
                job.error = f"Budget of ${job.budget_cap_usd} exceeded during discovery."
                return job

            for place in places:
                try:
                    await self._process_place(job, validated, place, cost_tracker)
                except BudgetExceededError:
                    log.warning("job_budget_exceeded", job_id=str(job.id))
                    job.status = "budget_exceeded"
                    job.error = f"Budget of ${job.budget_cap_usd} exceeded."
                    break
                except Exception as exc:
                    # Never let one bad entity kill the whole job — log and move on.
                    log.warning(
                        "job_entity_failed",
                        job_id=str(job.id),
                        place_id=place.id,
                        error=str(exc),
                    )

            if job.status == "running":
                try:
                    merged = await dedupe_job(self._session, job.id)
                    if merged:
                        log.info("job_dedupe_done", job_id=str(job.id), merged=merged)
                except Exception as exc:
                    # Dedupe is best-effort; never fail a job over it.
                    log.warning("job_dedupe_failed", job_id=str(job.id), error=str(exc))
                job.status = "succeeded"

        except Exception as exc:
            log.exception("job_failed", job_id=str(job.id))
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = _now()
            await self._session.flush()

        return job

    async def _validate(self, job: Job) -> ValidatedQuery | None:
        validated = await self._validator.validate(
            QueryRequest(query=job.query_raw, limit=job.limit)
        )
        if isinstance(validated, QueryValidationError):
            job.status = "rejected"
            job.error = validated.reason
            job.query_validated = {
                "status": "rejected",
                "reason": validated.reason,
                "suggestions": validated.suggestions,
            }
            return None
        job.query_validated = validated.model_dump()
        return validated

    async def _discover(
        self,
        job: Job,
        validated: ValidatedQuery,
        cost: _CostTracker,
    ) -> list[Place]:
        query_string = _query_string_for_places(validated)
        places = await self._places.text_search(
            query_string,
            region_code=validated.country,
            max_results=validated.limit,
            job_id=job.id,
        )
        cost.add(TEXT_SEARCH_COST_USD)
        job.cost_usd = cost.total  # type: ignore[assignment]
        if cost.over_cap:
            raise BudgetExceededError
        return places

    async def _process_place(
        self,
        job: Job,
        validated: ValidatedQuery,
        place: Place,
        cost: _CostTracker,
    ) -> None:
        domain = _domain_of(place.website_uri)

        if await is_blacklisted(self._session, domain=domain):
            log.info("job_place_skipped_blacklisted", job_id=str(job.id), domain=domain)
            return

        pages = await self._crawl(job, place)
        contacts = await self._extractor.extract(pages)

        primary_email = contacts.emails[0] if contacts.emails else None
        if primary_email and await is_blacklisted(self._session, email=primary_email):
            log.info("job_place_skipped_blacklisted_email", job_id=str(job.id), email=primary_email)
            return

        email_verification: EmailVerification | None = None
        if self._verify_emails and primary_email:
            try:
                email_verification = await verify_email(primary_email)
            except Exception as exc:  # DNS hiccups must never kill a job.
                log.warning("email_verify_failed", email=primary_email, error=str(exc))

        phone_raw = contacts.phones[0] if contacts.phones else place.national_phone_number
        phone_region = place.country_code() or validated.country
        phone_verification: PhoneVerification | None = None
        if phone_raw:
            try:
                phone_verification = verify_phone(phone_raw, region=phone_region)
            except Exception as exc:  # Defensive — libphonenumber shouldn't raise beyond parse.
                log.warning("phone_verify_failed", phone=phone_raw, error=str(exc))

        url_liveness: UrlLiveness | None = None
        if place.website_uri:
            url_liveness = liveness_from_crawl(place.website_uri, pages)

        entity = _build_entity(
            job_id=job.id,
            place=place,
            contacts=contacts,
            validated=validated,
            email_verification=email_verification,
            phone_verification=phone_verification,
            url_liveness=url_liveness,
        )
        self._session.add(entity)
        await self._session.flush()
        job.cost_usd = cost.total  # type: ignore[assignment]

    async def _crawl(self, job: Job, place: Place) -> list[CrawlResult]:
        if not place.website_uri:
            return []
        return await self._crawler.crawl_entity_site(place.website_uri, job_id=job.id)


class _CostTracker:
    def __init__(self, cap: float) -> None:
        self._cap = cap
        self._total = 0.0

    def add(self, amount: float) -> None:
        self._total += amount

    @property
    def total(self) -> float:
        return round(self._total, 6)

    @property
    def over_cap(self) -> bool:
        return self._total > self._cap


def _query_string_for_places(q: ValidatedQuery) -> str:
    parts: list[str] = [q.entity_type]
    parts.extend(q.keywords)
    location_bits: list[str] = []
    if q.city:
        location_bits.append(q.city)
    if q.region and q.region != q.city:
        location_bits.append(q.region)
    if location_bits:
        parts.append("in " + ", ".join(location_bits))
    return " ".join(p for p in parts if p).strip()


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    if not host:
        return None
    return host.removeprefix("www.")


def _build_entity(
    *,
    job_id: Any,
    place: Place,
    contacts: ExtractedContacts,
    validated: ValidatedQuery,
    email_verification: EmailVerification | None = None,
    phone_verification: PhoneVerification | None = None,
    url_liveness: UrlLiveness | None = None,
) -> Entity:
    now_iso = _now().isoformat()
    domain = _domain_of(place.website_uri)

    # If verification says the syntax is broken, treat as no email.
    if email_verification is not None and email_verification.status == "invalid_syntax":
        valid_emails = contacts.emails[1:] if len(contacts.emails) > 1 else []
    else:
        valid_emails = contacts.emails

    email = valid_emails[0] if valid_emails else None
    phone_raw = contacts.phones[0] if contacts.phones else place.national_phone_number
    phone_from_crawler = bool(contacts.phones)
    # Prefer E.164 from verification when it parsed successfully.
    phone = (
        phone_verification.e164
        if phone_verification and phone_verification.e164
        else phone_raw
    )

    field_sources: dict[str, Any] = {
        "name": {"source": "google_places", "fetched_at": now_iso, "confidence": 0.98},
    }
    if place.formatted_address:
        field_sources["address"] = {
            "source": "google_places",
            "fetched_at": now_iso,
            "confidence": 0.95,
        }
    if place.website_uri:
        website_base_conf = 0.99
        website_boost = url_liveness.confidence_boost if url_liveness else 1.0
        website_entry: dict[str, Any] = {
            "source": "google_places",
            "fetched_at": now_iso,
            "confidence": round(max(0.0, min(1.0, website_base_conf * website_boost)), 3),
        }
        if url_liveness is not None:
            website_entry["liveness"] = {
                "status": url_liveness.status,
                "http_status": url_liveness.http_status,
            }
        field_sources["website"] = website_entry
    if email:
        base_conf = 0.75 if contacts.used_llm else 0.90
        boost = email_verification.confidence_boost if email_verification else 1.0
        entry: dict[str, Any] = {
            "source": "llm_extractor" if contacts.used_llm else "crawler",
            "fetched_at": now_iso,
            "confidence": round(max(0.0, min(1.0, base_conf * boost)), 3),
        }
        if email_verification is not None:
            entry["verification"] = {
                "status": email_verification.status,
                "mx_host": email_verification.mx_host,
            }
        field_sources["email"] = entry
    if phone:
        phone_base_conf = 0.90
        phone_boost = phone_verification.confidence_boost if phone_verification else 1.0
        phone_entry: dict[str, Any] = {
            "source": "crawler" if phone_from_crawler else "google_places",
            "fetched_at": now_iso,
            "confidence": round(max(0.0, min(1.0, phone_base_conf * phone_boost)), 3),
        }
        if phone_verification is not None:
            phone_entry["verification"] = {
                "status": phone_verification.status,
                "kind": phone_verification.kind,
                "region": phone_verification.region,
            }
        field_sources["phone"] = phone_entry

    socials_payload = {s.platform: s.url for s in contacts.socials} if contacts.socials else None

    city = place.city() or validated.city
    country = place.country_code() or validated.country
    address = place.formatted_address

    values = {
        "name": place.name or None,
        "website": place.website_uri,
        "email": email,
        "phone": phone,
        "address": address,
        "city": city,
        "country": country,
    }
    quality_score = score_entity(values=values, field_sources=field_sources)

    return Entity(
        job_id=job_id,
        name=place.name or "(unnamed)",
        domain=domain,
        website=place.website_uri,
        email=email,
        phone=phone,
        address=address,
        city=city,
        country=country,
        category=place.primary_type,
        socials=socials_payload,
        quality_score=quality_score,
        review_status=review_status_for(quality_score),
        field_sources=field_sources,
        external_ids={"google_place_id": place.id},
    )


def _now() -> datetime:
    return datetime.now(UTC)
