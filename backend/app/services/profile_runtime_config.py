"""Safe assembly of the effective runtime configuration for strategy profiles."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from decimal import Decimal
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

BLOCK_RULE_CONFIG_CONFLICT = "BLOCK_RULE_CONFIG_CONFLICT"
PROFILE_BLOCK_RULES_DROPPED = "PROFILE_BLOCK_RULES_DROPPED"
BLOCK_RULES_CONTRACT_VERSION = "profile_block_rules_merge_v2"
ENTRY_TRIGGERS_CONTRACT_VERSION = "profile_plus_global_entry_triggers_v1"
TRANSIENT_PROFILE_METADATA_KEYS = frozenset(
    {
        "_execution_contract",
        "_block_rules_lineage",
        "_entry_triggers_lineage",
        "_global_entry_triggers",
        "_l3_gate_runtime_policy",
    }
)


class BlockRuleConfigConflict(ValueError):
    """Raised when one rule identity has incompatible definitions."""

    code = BLOCK_RULE_CONFIG_CONFLICT

    def __init__(
        self,
        *,
        rule_id: str,
        global_definition_hash: str,
        profile_definition_hash: str,
        profile_id: Any = None,
        profile_version_id: Any = None,
    ) -> None:
        self.rule_id = rule_id
        self.global_definition_hash = global_definition_hash
        self.profile_definition_hash = profile_definition_hash
        self.profile_id = profile_id
        self.profile_version_id = profile_version_id
        super().__init__(
            f"{self.code}: rule_id={rule_id} "
            f"global_definition_hash={global_definition_hash} "
            f"profile_definition_hash={profile_definition_hash}"
        )


class ProfileBlockRulesDropped(ValueError):
    """Raised when effective assembly cannot prove profile-rule preservation."""

    code = PROFILE_BLOCK_RULES_DROPPED


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            return str(value)
        if number == 0:
            return 0
        if number == number.to_integral():
            return int(number)
        return float(number.normalize())
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def canonical_hash(value: Any) -> str:
    """Return the stable SHA-256 used by block-rule lineage."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_profile_config_hash(config: Mapping[str, Any] | None) -> str:
    """Hash executable profile content, excluding only catalogued runtime keys."""

    source = dict(config or {})
    persisted = {
        key: value
        for key, value in source.items()
        if key not in TRANSIENT_PROFILE_METADATA_KEYS
    }
    return canonical_hash(persisted)


def _extract_blocks(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        blocks = value.get("blocks", [])
    else:
        blocks = value
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise TypeError("block_rules.blocks must be an array")
    if not all(isinstance(block, Mapping) for block in blocks):
        raise TypeError("every block rule must be an object")
    return [deepcopy(dict(block)) for block in blocks]


def _rule_identity(rule: Mapping[str, Any]) -> str:
    explicit_id = str(rule.get("id") or "").strip()
    if explicit_id:
        return f"id:{explicit_id}"
    identity_material = {
        "name": rule.get("name"),
        "timeframe": rule.get("timeframe"),
        "logic": rule.get("logic"),
        "conditions": rule.get("conditions"),
    }
    return f"content:{canonical_hash(identity_material)}"


def canonical_block_rules_hash(blocks: Sequence[Mapping[str, Any]]) -> str:
    return canonical_hash({"blocks": list(blocks)})


def merge_block_rules(
    *,
    global_blocks: Sequence[Mapping[str, Any]],
    profile_blocks: Sequence[Mapping[str, Any]],
    profile_id: Any = None,
    profile_version_id: Any = None,
) -> list[dict[str, Any]]:
    """Compose global-first rules, dedupe exact matches, and reject conflicts."""

    effective: list[dict[str, Any]] = []
    seen: dict[str, tuple[str, str]] = {}

    for source, raw_rules in (
        ("global", global_blocks),
        ("profile", profile_blocks),
    ):
        for raw_rule in raw_rules:
            rule = deepcopy(dict(raw_rule))
            identity = _rule_identity(rule)
            definition_hash = canonical_hash(rule)
            previous = seen.get(identity)
            if previous is None:
                seen[identity] = (source, definition_hash)
                effective.append(rule)
                continue
            previous_source, previous_hash = previous
            if previous_hash == definition_hash:
                continue
            global_hash = previous_hash if previous_source == "global" else definition_hash
            profile_hash = definition_hash if source == "profile" else previous_hash
            public_rule_id = identity.removeprefix("id:")
            conflict = BlockRuleConfigConflict(
                rule_id=public_rule_id,
                global_definition_hash=global_hash,
                profile_definition_hash=profile_hash,
                profile_id=profile_id,
                profile_version_id=profile_version_id,
            )
            logger.error(
                "[%s] rule_id=%s global_definition_hash=%s "
                "profile_definition_hash=%s profile_id=%s profile_version_id=%s",
                conflict.code,
                public_rule_id,
                global_hash,
                profile_hash,
                profile_id,
                profile_version_id,
            )
            try:
                from .l3_gate_v2_metrics import observe_block_config_failure

                observe_block_config_failure(conflict.code)
            except Exception:
                logger.debug("failed to emit block-rule conflict metric", exc_info=True)
            raise conflict

    effective_identities = {_rule_identity(rule) for rule in effective}
    missing_profile_identities = {
        _rule_identity(rule) for rule in profile_blocks
    } - effective_identities
    if missing_profile_identities:
        logger.critical(
            "[%s] profile_id=%s profile_version_id=%s missing_rule_ids=%s",
            PROFILE_BLOCK_RULES_DROPPED,
            profile_id,
            profile_version_id,
            sorted(missing_profile_identities),
        )
        try:
            from .l3_gate_v2_metrics import observe_block_config_failure

            observe_block_config_failure(PROFILE_BLOCK_RULES_DROPPED)
        except Exception:
            logger.debug("failed to emit dropped-rule metric", exc_info=True)
        raise ProfileBlockRulesDropped(
            f"{PROFILE_BLOCK_RULES_DROPPED}: {sorted(missing_profile_identities)}"
        )

    return effective


def _entry_trigger_count(value: Any) -> int:
    """Return the number of configured entry-trigger conditions."""
    if isinstance(value, Mapping):
        conditions = value.get("conditions")
        return len(conditions) if isinstance(conditions, list) else 0
    return len(value) if isinstance(value, list) else 0


def merge_profile_runtime_block_config(
    profile_config: Mapping[str, Any] | None,
    global_block_config: Mapping[str, Any] | None,
    *,
    profile_id: Any = None,
    profile_version_id: Any = None,
) -> dict[str, Any]:
    """Build global/profile contracts without replacing profile entry gates."""
    merged = deepcopy(dict(profile_config or {}))
    global_config = dict(global_block_config or {})
    profile_blocks = _extract_blocks(merged.get("block_rules"))
    global_blocks = _extract_blocks(global_config.get("block_rules"))
    effective_blocks = merge_block_rules(
        global_blocks=global_blocks,
        profile_blocks=profile_blocks,
        profile_id=profile_id,
        profile_version_id=profile_version_id,
    )
    merged["block_rules"] = {"blocks": effective_blocks}
    merged["_block_rules_lineage"] = {
        "contract_version": BLOCK_RULES_CONTRACT_VERSION,
        "profile_id": str(profile_id) if profile_id is not None else None,
        "profile_version_id": (
            str(profile_version_id) if profile_version_id is not None else None
        ),
        "profile_config_hash": canonical_profile_config_hash(profile_config),
        "profile_block_rules_hash": canonical_block_rules_hash(profile_blocks),
        "global_block_rules_hash": canonical_block_rules_hash(global_blocks),
        "effective_block_rules_hash": canonical_block_rules_hash(effective_blocks),
        "profile_rules_count": len(profile_blocks),
        "global_rules_count": len(global_blocks),
        "effective_rules_count": len(effective_blocks),
        "reason_codes": [],
    }

    profile_entry_triggers = deepcopy(merged.get("entry_triggers") or {})
    global_entry_triggers = deepcopy(global_config.get("entry_triggers") or {})
    # L3_CONFIG_GUARD: profile triggers stay intact; global triggers are AND-only.
    merged["entry_triggers"] = profile_entry_triggers
    merged["_global_entry_triggers"] = global_entry_triggers
    merged["_entry_triggers_lineage"] = {
        "contract_version": ENTRY_TRIGGERS_CONTRACT_VERSION,
        "profile_id": str(profile_id) if profile_id is not None else None,
        "profile_version_id": (
            str(profile_version_id) if profile_version_id is not None else None
        ),
        "profile_entry_triggers_hash": canonical_hash(profile_entry_triggers),
        "global_entry_triggers_hash": canonical_hash(global_entry_triggers),
        "profile_trigger_count": _entry_trigger_count(profile_entry_triggers),
        "global_trigger_count": _entry_trigger_count(global_entry_triggers),
        "composition": "AND",
    }
    return merged
