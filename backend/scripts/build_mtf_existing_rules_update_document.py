"""Build an update-only JSON for the existing L1/L2 profiles.

The source document is treated only as a rule template.  The generated
document targets profiles by immutable ID and compare-and-swap identity, sets
``update_indicators_only=true``, and therefore cannot create profiles.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.profile_indicator_contract import (  # noqa: E402
    validate_profile_execution_structure,
)
from app.services.profile_execution_contract import EXECUTION_SECTIONS  # noqa: E402
from app.services.profile_runtime_config import canonical_hash  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    for layer in ("l1", "l2"):
        parser.add_argument(f"--{layer}-profile-id", required=True, type=UUID)
        parser.add_argument(f"--{layer}-version-id", required=True, type=UUID)
        parser.add_argument(f"--{layer}-config-hash", required=True)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("SOURCE_DOCUMENT_MUST_BE_AN_OBJECT")
    return payload


def _profiles_by_layer(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise SystemExit("SOURCE_PROFILES_MUST_BE_AN_ARRAY")
    by_layer: dict[str, dict[str, Any]] = {}
    for item in raw_profiles:
        if not isinstance(item, dict):
            raise SystemExit("SOURCE_PROFILE_MUST_BE_AN_OBJECT")
        layer = str(item.get("layer") or "").upper()
        if layer in by_layer:
            raise SystemExit(f"SOURCE_LAYER_DUPLICATED:{layer}")
        if layer in {"L1", "L2"}:
            by_layer[layer] = item
    if set(by_layer) != {"L1", "L2"}:
        raise SystemExit("SOURCE_MUST_CONTAIN_EXACTLY_L1_AND_L2")
    return by_layer


def _hash(value: str, field: str) -> str:
    normalized = str(value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise SystemExit(f"{field}_INVALID")
    return normalized


def main() -> int:
    args = _args()
    source = _load_json(args.source)
    by_layer = _profiles_by_layer(source)
    targets = {
        "L1": {
            "profile_id": str(args.l1_profile_id),
            "expected_profile_version_id": str(args.l1_version_id),
            "expected_profile_config_hash": _hash(
                args.l1_config_hash, "L1_CONFIG_HASH"
            ),
        },
        "L2": {
            "profile_id": str(args.l2_profile_id),
            "expected_profile_version_id": str(args.l2_version_id),
            "expected_profile_config_hash": _hash(
                args.l2_config_hash, "L2_CONFIG_HASH"
            ),
        },
    }

    profiles: list[dict[str, Any]] = []
    section_counts: dict[str, dict[str, int]] = {}
    for layer in ("L1", "L2"):
        template = by_layer[layer]
        item: dict[str, Any] = {
            **targets[layer],
            "name": layer,
            **{
                section: deepcopy(template.get(section))
                for section in EXECUTION_SECTIONS
            },
        }
        missing = [section for section in EXECUTION_SECTIONS if item.get(section) is None]
        if missing:
            raise SystemExit(f"SOURCE_EXECUTION_SECTIONS_MISSING:{layer}:{','.join(missing)}")
        errors = validate_profile_execution_structure(
            item, path=f"profiles[{len(profiles)}]", require_sections=True
        )
        if errors:
            raise SystemExit(
                "PROFILE_CONDITION_INVALID:"
                + json.dumps(errors, ensure_ascii=False, sort_keys=True)
            )
        profiles.append(item)
        section_counts[layer] = {
            "filters": len((item["filters"] or {}).get("conditions") or []),
            "signals": len((item["signals"] or {}).get("conditions") or []),
            "entry_triggers": len(
                (item["entry_triggers"] or {}).get("conditions") or []
            ),
            "block_rules": len((item["block_rules"] or {}).get("blocks") or []),
        }

    document = {
        "update_indicators_only": True,
        "allow_duplicate_names": False,
        "_documentation": {
            "purpose": (
                "Atualizar atomicamente as regras dos profiles existentes L1 e L2, "
                "sem criar novos profiles."
            ),
            "source_template": args.source.name,
            "import_endpoint": "POST /api/profiles/bulk-import",
            "update_scope": list(EXECUTION_SECTIONS),
            "preserved_fields": [
                "profile_id",
                "name",
                "profile_type",
                "profile_role",
                "default_timeframe",
                "scoring",
                "mtf_semantics",
                "source_identity",
                "activation state",
                "watchlist associations",
                "MTF contract",
            ],
            "calibration_warning": (
                "As regras e thresholds vieram do documento de proposta e não foram "
                "aprovados por uma calibração PASSED. Este arquivo carrega regras; "
                "não ativa o contrato MTF."
            ),
            "compare_and_swap": (
                "O upload falha integralmente se qualquer versão ou hash esperado "
                "não corresponder ao estado atual."
            ),
        },
        "profiles": profiles,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "EMITTED",
                "output": str(args.output),
                "document_hash": canonical_hash(document),
                "profiles": len(profiles),
                "allow_create": False,
                "section_counts": section_counts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
