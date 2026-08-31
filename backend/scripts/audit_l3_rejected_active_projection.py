"""Read-only proof for the L3_REJECTED active read projection."""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session

from app.api.shadow_trades import (
    _ACTIVE_STATUSES,
    _active_read_projection,
    _legacy_active_rank_key,
    _rejected_projected_ids_query,
)
from app.models.backoffice import DecisionLog
from app.models.shadow_trade import ShadowTrade


def _sync_database_url() -> str:
    raw = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL missing")
    if raw.startswith("postgresql+asyncpg://"):
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def main() -> None:
    evidence: list[dict[str, object]] = []
    engine = create_engine(_sync_database_url(), pool_pre_ping=True)
    with Session(engine) as db:
        users = db.execute(
            select(ShadowTrade.user_id)
            .where(
                ShadowTrade.source == "L3_REJECTED",
                ShadowTrade.status.in_(_ACTIVE_STATUSES),
            )
            .distinct()
        ).scalars().all()
        for user_id in users:
            raw_active = int(
                db.execute(
                    select(func.count(ShadowTrade.id)).where(
                        ShadowTrade.user_id == user_id,
                        ShadowTrade.source == "L3_REJECTED",
                        ShadowTrade.status.in_(_ACTIVE_STATUSES),
                    )
                ).scalar_one()
                or 0
            )
            projected_ids = _rejected_projected_ids_query(
                user_id=user_id,
                status="OPEN",
                symbol=None,
                min_date=None,
                max_date=None,
                profile_id=None,
                profile_version=None,
            ).subquery()
            projected_active = int(
                db.execute(
                    select(func.count()).select_from(projected_ids)
                ).scalar_one()
                or 0
            )
            winners = db.execute(
                select(ShadowTrade)
                .join(projected_ids, projected_ids.c.id == ShadowTrade.id)
                .order_by(ShadowTrade.created_at.desc())
                .limit(20)
            ).scalars().all()
            group_conditions = [
                (
                    (ShadowTrade.user_id == row.user_id)
                    & (ShadowTrade.symbol == row.symbol)
                    & (
                        func.coalesce(ShadowTrade.direction, "SPOT")
                        == (row.direction or "SPOT")
                    )
                )
                for row in winners
            ]
            members = (
                db.execute(
                    select(ShadowTrade).where(
                        ShadowTrade.source == "L3_REJECTED",
                        ShadowTrade.status.in_(_ACTIVE_STATUSES),
                        or_(*group_conditions),
                    )
                ).scalars().all()
                if group_conditions
                else []
            )
            decision_ids = {row.decision_id for row in members if row.decision_id}
            decisions = {
                row.id: row
                for row in db.execute(
                    select(DecisionLog).where(DecisionLog.id.in_(decision_ids))
                ).scalars().all()
            }
            grouped: dict[tuple[str, str, str], list[ShadowTrade]] = {}
            for member in members:
                key = (str(member.user_id), member.symbol, member.direction or "SPOT")
                grouped.setdefault(key, []).append(member)
            projections = {}
            for row in winners:
                key = (str(row.user_id), row.symbol, row.direction or "SPOT")
                group = grouped[key]
                primary = sorted(
                    group, key=lambda item: _legacy_active_rank_key(item, decisions)
                )[0]
                projections[row.id] = _active_read_projection(
                    primary, group, decisions
                )
            duplicate_symbols = [
                {
                    "symbol": row.symbol,
                    "direction": row.direction,
                    "primary_profile": projections[row.id]["primary_profile"][
                        "profile_name"
                    ],
                    "associated_count": projections[row.id]["associated_count"],
                }
                for row in winners
                if projections[row.id]["associated_count"] > 0
            ]
            evidence.append(
                {
                    "user_id": str(user_id),
                    "raw_active_rows": raw_active,
                    "projected_active_rows": projected_active,
                    "duplicate_rows_removed_from_read": raw_active
                    - projected_active,
                    "sample_projected_groups": duplicate_symbols[:10],
                }
            )
    print(json.dumps({"status": "PASS", "users": evidence}, ensure_ascii=False))


if __name__ == "__main__":
    main()
