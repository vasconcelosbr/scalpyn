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

def main(audit_kind: str = "native_capture_canary"):
    p=argparse.ArgumentParser(); p.add_argument("--from",dest="start",default=os.getenv("NATIVE_CAPTURE_START_AT")); p.add_argument("--limit",type=int,default=50); p.add_argument("--dry-run",action="store_true",required=True); p.add_argument("--format",choices=("json","markdown"),default="json")
    args = p.parse_args()
    result=asyncio.run(run(args.start,args.limit)); result["audit_kind"]=audit_kind; result["dry_run"]=True
    if args.format == "markdown":
        print("# Audit " + audit_kind + "\n\n" + "\n".join(f"- {k}: `{v}`" for k,v in result.items()))
    else: print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__ == "__main__": main()
