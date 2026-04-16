"""Live end-to-end smoke test.

Submits a POST /jobs request to a running instance of the server, polls
GET /jobs/{id} until the job finishes, then downloads the CSV export.

Run the server first in another terminal:
    uv run uvicorn app.main:app --reload

Then:
    uv run python scripts/smoke.py "restaurants in Paris" --limit 10 --budget 2

Requires the real GOOGLE_PLACES_API_KEY and ANTHROPIC_API_KEY env vars to be
set for the server process (i.e. in .env).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx


async def run(
    *,
    base_url: str,
    query: str,
    limit: int,
    budget: float,
    out_path: Path,
    poll_seconds: float,
) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        print(f"→ submitting POST {base_url}/jobs")
        resp = await client.post(
            "/jobs",
            json={"query": query, "limit": limit, "budget_cap_usd": budget},
        )
        resp.raise_for_status()
        job = resp.json()
        job_id = job["id"]
        print(f"  job_id={job_id} status={job['status']}")

        started = time.monotonic()
        last_status = None
        while True:
            await asyncio.sleep(poll_seconds)
            poll = await client.get(f"/jobs/{job_id}")
            poll.raise_for_status()
            body = poll.json()
            if body["status"] != last_status:
                last_status = body["status"]
                elapsed = time.monotonic() - started
                print(
                    f"  [{elapsed:6.1f}s] status={body['status']} "
                    f"cost=${body['cost_usd']:.4f} "
                    f"entities={body['entity_count']}"
                )
            if body["status"] in {"succeeded", "failed", "rejected", "budget_exceeded"}:
                break

        if body["status"] != "succeeded":
            print(f"✗ job ended with status '{body['status']}'", file=sys.stderr)
            if body.get("error"):
                print(f"  error: {body['error']}", file=sys.stderr)
            return 2

        print(f"→ downloading CSV to {out_path}")
        csv_resp = await client.get(f"/jobs/{job_id}/export.csv")
        csv_resp.raise_for_status()
        out_path.write_bytes(csv_resp.content)

        row_count = max(0, csv_resp.text.count("\n") - 1)
        print(f"✓ done — {row_count} rows, ${body['cost_usd']:.4f} total, saved to {out_path}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("query", help="Natural-language lead-gen query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--budget", type=float, default=2.0, help="USD cap per job")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="smoke.csv", help="CSV output path")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    return asyncio.run(
        run(
            base_url=args.base_url,
            query=args.query,
            limit=args.limit,
            budget=args.budget,
            out_path=Path(args.out),
            poll_seconds=args.poll_seconds,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
