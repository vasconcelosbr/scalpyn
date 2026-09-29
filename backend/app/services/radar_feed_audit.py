"""RADAR_FEED_AUDIT: immutable receipts; reconciliation is a separate observation."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, delete
from ..models.radar_feed_audit import RadarFeedReceipt, RadarFeedItem
from ..models.pool import PoolCoin

# User-requested data-retention policy, independent of trading thresholds.
RETENTION = timedelta(days=7)
DISPLAY_TZ = timezone(timedelta(hours=-3))


def parse_source_updated_at(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def display_time(value):
    if value is None:
        return None
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ).strftime("%d/%m/%Y, %H:%M:%S GMT-3")


async def record_receipt(db, *, pool_id, assets, received_at, selected_pairs, reason=None):
    receipt_id = uuid4()
    rows = assets if assets is not None else []
    db.add(RadarFeedReceipt(
        id=receipt_id, pool_id=pool_id, received_at=received_at,
        expires_at=received_at + RETENTION,
        status="RECEIVED" if assets is not None else "UNAVAILABLE",
        source_count=len(rows), reason=reason,
    ))
    await db.flush()
    # Preserve every returned entry, including filtered pairs and repeated symbols.
    db.add_all([RadarFeedItem(
        receipt_id=receipt_id, symbol=asset["pair"],
        source_updated_at=parse_source_updated_at(asset.get("updated_at")),
        pool_result="PENDING" if asset["pair"] in selected_pairs else "FILTERED",
    ) for asset in rows])
    await db.flush()
    return receipt_id


async def complete_receipt(db, receipt_id, *, reconciled, skipped=False):
    receipt = await db.get(RadarFeedReceipt, receipt_id)
    if receipt is None:
        return
    receipt.status = "SYNCED" if reconciled and not skipped else "SYNC_FAILED" if not reconciled else "SKIPPED"
    items = (await db.scalars(select(RadarFeedItem).where(RadarFeedItem.receipt_id == receipt_id))).all()
    coins = (await db.scalars(select(PoolCoin).where(PoolCoin.pool_id == receipt.pool_id))).all()
    present = {c.symbol for c in coins if c.is_active and c.origin == "radar" and not c.held_for_open_position}
    for item in items:
        if item.pool_result != "PENDING":
            continue
        item.pool_result = ("PRESENT" if item.symbol in present else "NOT_INCLUDED") if reconciled and not skipped else "NOT_VERIFIED"
    await db.flush()


async def purge_expired(db, now=None):
    # A dedicated periodic task also runs when no radar pool remains enabled.
    result = await db.execute(delete(RadarFeedReceipt).where(
        RadarFeedReceipt.expires_at <= (now or datetime.now(timezone.utc))))
    return result.rowcount
