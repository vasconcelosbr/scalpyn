"""Read-only native capture canary: python -m scripts.audit_native_capture_canary --dry-run."""
import argparse, asyncio, json, os
from datetime import datetime
from app.database import AsyncSessionLocal
from app.ml.native_capture_governance import audit_native_capture

async def run(start: str | None, limit: int) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            value = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            return await audit_native_capture(db, value, limit)
        finally: await db.rollback()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--from",dest="start",default=os.getenv("NATIVE_CAPTURE_START_AT")); p.add_argument("--limit",type=int,default=50); p.add_argument("--dry-run",action="store_true",required=True)
    args = p.parse_args()
    print(json.dumps(asyncio.run(run(args.start,args.limit)),ensure_ascii=False,indent=2))
if __name__ == "__main__": main()
