"""Backfill embeddings for entities that don't have one yet.

Daily cron companion to the runner. Picks the oldest N entities whose
`embedding` is NULL, embeds them in batches via OpenAI, and writes the
result back. Per-row failures are logged and skipped — one bad row
doesn't poison the batch.

Usage:
    uv run python scripts/embed_entities.py
    uv run python scripts/embed_entities.py --batch-size 50 --max-rows 500
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import bindparam, text

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import session_scope
from app.services.embeddings import OpenAIEmbeddings, entity_to_embed_text

DEFAULT_BATCH = 50
DEFAULT_MAX = 500


async def main_async(batch_size: int, max_rows: int) -> None:
    configure_logging()
    log = get_logger("embed_entities")
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set — nothing to do.")
        return

    client = OpenAIEmbeddings(api_key=settings.openai_api_key)
    total_embedded = 0

    select_stmt = text(
        """
        SELECT id, name, city, country, category
        FROM entities
        WHERE embedding IS NULL
        ORDER BY created_at ASC
        LIMIT :batch
        """
    ).bindparams(bindparam("batch"))
    update_stmt = text(
        "UPDATE entities SET embedding = CAST(:vec AS vector) WHERE id = :id"
    ).bindparams(bindparam("vec"), bindparam("id"))

    async with session_scope() as session:
        for _ in range((max_rows + batch_size - 1) // batch_size):
            rows = (
                await session.execute(select_stmt, {"batch": batch_size})
            ).mappings().all()
            if not rows:
                break

            # Skip rows with no embeddable text — embedding "" wastes tokens
            # AND yields near-identical vectors that pollute similarity
            # search. Leave NULL so a future fix to entity_to_embed_text
            # picks them up on the next run.
            class _Tmp:
                __slots__ = ("name", "city", "country", "category")

            def _to_obj(r):
                obj = _Tmp()
                obj.name, obj.city = r["name"], r["city"]
                obj.country, obj.category = r["country"], r["category"]
                return obj

            keepers = [
                (r, t) for r, t in (
                    (r, entity_to_embed_text(_to_obj(r))) for r in rows
                ) if t
            ]
            if not keepers:
                continue

            try:
                vectors = await client.embed([t for _, t in keepers])
            except Exception as exc:
                log.warning("embed_batch_failed", error=str(exc))
                break

            for (r, _text), vec in zip(keepers, vectors, strict=True):
                vec_lit = "[" + ",".join(repr(float(x)) for x in vec) + "]"
                await session.execute(update_stmt, {"vec": vec_lit, "id": r["id"]})
            await session.commit()
            total_embedded += len(keepers)
            log.info("embed_batch_done", rows=len(keepers))

    log.info("embed_script_done", total_embedded=total_embedded)
    print(f"embedded {total_embedded} entities")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX)
    args = parser.parse_args()
    asyncio.run(main_async(args.batch_size, args.max_rows))


if __name__ == "__main__":
    main()
