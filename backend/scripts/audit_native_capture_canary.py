"""Read-only native capture auditor."""

import argparse
import asyncio
import json
import os
from datetime import datetime

from app.database import AsyncSessionLocal
from app.ml.native_capture_governance import audit_native_capture


async def run(
    start: str | None,
    limit: int,
    full_window: bool,
    audit_query_cutoff: str | None,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            value = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            return await audit_native_capture(
                db,
                value,
                limit,
                full_window=full_window,
                audit_query_cutoff=(
                    datetime.fromisoformat(audit_query_cutoff.replace("Z", "+00:00"))
                    if audit_query_cutoff
                    else None
                ),
            )
        finally:
            await db.rollback()


def main(audit_kind: str = "native_capture_canary"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default=os.getenv("NATIVE_CAPTURE_START_AT"))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--full-window", action="store_true")
    parser.add_argument("--as-of", dest="audit_query_cutoff")
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.start,
            args.limit,
            args.full_window,
            args.audit_query_cutoff,
        )
    )
    result["audit_kind"] = audit_kind
    result["dry_run"] = True
    result["shared_core"] = "app.ml.native_capture_governance.audit_native_capture"
    if args.format == "markdown":
        print(
            "# Audit "
            + audit_kind
            + "\n\n"
            + "\n".join(f"- {key}: `{value}`" for key, value in result.items())
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
