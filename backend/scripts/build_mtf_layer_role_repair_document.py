"""Build a governed L1/L2 role repair from the current persisted profiles.

The script is read-only with respect to PostgreSQL.  It copies the current
economic material and compare-and-swap identities, removes only sections that
do not belong to the layer role, and validates the final activation document.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Local production audits must use Railway's public endpoint; the private host
# is resolvable only inside the Railway network.
if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.profile import Profile  # noqa: E402
from app.services.mtf_profile_activation_service import (  # noqa: E402
    WAIVER_ECONOMIC_SECTIONS,
    parse_activation_document,
)
from app.services.profile_execution_contract import (  # noqa: E402
    EXECUTION_SECTIONS,
    load_profile_execution_snapshots,
)
from app.services.profile_runtime_config import canonical_hash  # noqa: E402


PRESERVED_SECTIONS = {
    layer: (*sections, "mtf_semantics")
    for layer, sections in WAIVER_ECONOMIC_SECTIONS.items()
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=UUID)
    parser.add_argument("--base-document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("JSON_OBJECT_REQUIRED")
    return payload


def _preserved_material(config: Mapping[str, Any], layer: str) -> dict[str, Any]:
    return {
        section: deepcopy(config.get(section) or {})
        for section in PRESERVED_SECTIONS[layer]
    }


def repair_layer_config(config: Mapping[str, Any], layer: str) -> dict[str, Any]:
    """Remove only execution sections that are not valid for the layer role."""

    repaired = deepcopy(dict(config))
    if layer == "L1":
        repaired["signals"] = {"logic": "AND", "conditions": []}
        repaired["entry_triggers"] = {"logic": "AND", "conditions": []}
    elif layer == "L2":
        repaired["entry_triggers"] = {"logic": "AND", "conditions": []}
    else:
        raise ValueError(f"UNSUPPORTED_MTF_LAYER:{layer}")
    return repaired


async def _run(args: argparse.Namespace) -> None:
    base = _load_json(args.base_document)
    base_profiles = base.get("profiles")
    base_watchlists = base.get("watchlists")
    if not isinstance(base_profiles, Mapping) or set(base_profiles) != {"L1", "L2"}:
        raise SystemExit("BASE_PROFILES_MUST_CONTAIN_L1_L2")
    if not isinstance(base_watchlists, Mapping) or set(base_watchlists) != {"L1", "L2"}:
        raise SystemExit("BASE_WATCHLISTS_MUST_CONTAIN_L1_L2")

    profile_ids = {
        layer: UUID(str(base_profiles[layer]["profile_id"]))
        for layer in ("L1", "L2")
    }
    watchlist_ids = {
        layer: UUID(str(base_watchlists[layer]))
        for layer in ("L1", "L2")
    }

    async with AsyncSessionLocal() as db:
        statement = select(Profile).where(Profile.id.in_(list(profile_ids.values())))
        if args.user_id is not None:
            statement = statement.where(Profile.user_id == args.user_id)
        rows = (await db.execute(statement)).scalars().all()
        profiles = {row.id: row for row in rows}
        if set(profiles) != set(profile_ids.values()):
            raise SystemExit("MTF_PROFILE_NOT_FOUND")
        owners = {row.user_id for row in rows}
        if len(owners) != 1:
            raise SystemExit("MTF_PROFILE_OWNER_MISMATCH")
        user_id = next(iter(owners))
        snapshots = await load_profile_execution_snapshots(
            db, profile_ids.values(), user_id=user_id
        )
        if set(snapshots) != set(profile_ids.values()):
            raise SystemExit("MTF_PROFILE_VERSION_NOT_FOUND")

        watchlists = (await db.execute(text("""
            SELECT id, profile_id
              FROM pipeline_watchlists
             WHERE user_id = CAST(:user_id AS UUID)
               AND id IN (CAST(:l1 AS UUID), CAST(:l2 AS UUID))
        """), {
            "user_id": str(user_id),
            "l1": str(watchlist_ids["L1"]),
            "l2": str(watchlist_ids["L2"]),
        })).mappings().all()
        bindings = {row["id"]: row["profile_id"] for row in watchlists}
        if set(bindings) != set(watchlist_ids.values()):
            raise SystemExit("MTF_WATCHLIST_NOT_FOUND")

    document = deepcopy(base)
    document_profiles: dict[str, Any] = {}
    preservation_hashes: dict[str, str] = {}
    removed_condition_counts: dict[str, dict[str, int]] = {}
    for layer in ("L1", "L2"):
        profile = profiles[profile_ids[layer]]
        current = deepcopy(dict(profile.config or {}))
        repaired = repair_layer_config(current, layer)
        before_material = _preserved_material(current, layer)
        after_material = _preserved_material(repaired, layer)
        if before_material != after_material:
            raise SystemExit(f"ECONOMIC_MATERIAL_CHANGED:{layer}")
        preservation_hashes[layer] = canonical_hash(before_material)
        removed_condition_counts[layer] = {
            section: len((current.get(section) or {}).get("conditions") or [])
            for section in ("signals", "entry_triggers")
            if section not in PRESERVED_SECTIONS[layer]
        }
        snapshot = snapshots[profile.id]["contract"]
        item = {
            "profile_id": str(profile.id),
            "expected_profile_version_id": snapshot["profile_version_id"],
            "expected_profile_config_hash": snapshot["profile_projection_hash"],
            "name": layer,
            "profile_kind": "MTF_LAYER",
            "layer": layer,
            "funnel_role": "primary_filter" if layer == "L1" else "score_engine",
            "default_timeframe": "1h" if layer == "L1" else "15m",
            **{
                section: deepcopy(repaired.get(section) or {})
                for section in EXECUTION_SECTIONS
            },
            "scoring": deepcopy(repaired.get("scoring") or {}),
            "mtf_semantics": deepcopy(repaired.get("mtf_semantics") or {}),
            "source_identity": deepcopy(repaired.get("source_identity") or {}),
            "calibration": deepcopy(repaired.get("calibration") or {}),
            "activation_mode": "SHADOW",
            "is_shadow_only": True,
            "live_trading_enabled": False,
        }
        document_profiles[layer] = item

    document["profiles"] = document_profiles
    document["expected_watchlist_bindings"] = {
        layer: str(bindings[watchlist_ids[layer]]) for layer in ("L1", "L2")
    }
    documentation = deepcopy(document.get("_documentation") or {})
    documentation.update({
        "purpose": "Corrigir a semantica das secoes L1/L2 sem alterar thresholds.",
        "economic_material_preserved": True,
        "economic_material_hashes": preservation_hashes,
        "removed_wrong_role_condition_counts": removed_condition_counts,
        "next_import_prevalidated": True,
    })
    document["_documentation"] = documentation

    parse_activation_document(document)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "EMITTED",
        "output": str(args.output),
        "document_hash": canonical_hash(document),
        "profile_count": len(document_profiles),
        "user_id": str(user_id),
        "profile_ids": {layer: str(profile_ids[layer]) for layer in ("L1", "L2")},
        "expected_profile_versions": {
            layer: document_profiles[layer]["expected_profile_version_id"]
            for layer in ("L1", "L2")
        },
        "expected_profile_hashes": {
            layer: document_profiles[layer]["expected_profile_config_hash"]
            for layer in ("L1", "L2")
        },
        "economic_material_hashes": preservation_hashes,
        "removed_wrong_role_condition_counts": removed_condition_counts,
        "next_import_prevalidated": True,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run(_args()))
