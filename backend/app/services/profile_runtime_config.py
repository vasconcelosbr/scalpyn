"""Safe assembly of the effective runtime configuration for strategy profiles."""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _entry_trigger_count(value: Any) -> int:
    """Return the number of configured entry-trigger conditions."""
    if isinstance(value, Mapping):
        conditions = value.get("conditions")
        return len(conditions) if isinstance(conditions, list) else 0
    return len(value) if isinstance(value, list) else 0


def merge_profile_runtime_block_config(
    profile_config: Mapping[str, Any] | None,
    global_block_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge global Autopilot settings without erasing profile entry gates.

    A non-empty global ``entry_triggers`` value remains an intentional
    Autopilot override.  An explicitly empty global value, however, is not
    allowed to replace non-empty profile triggers: doing so silently turns the
    positive entry gate into an allow-by-default path.
    """
    merged = dict(profile_config or {})
    if not global_block_config:
        return merged

    block_rules = global_block_config.get("block_rules")
    if block_rules is not None:
        merged["block_rules"] = block_rules

    global_entry_triggers = global_block_config.get("entry_triggers")
    if global_entry_triggers is None:
        return merged

    profile_trigger_count = _entry_trigger_count(merged.get("entry_triggers"))
    global_trigger_count = _entry_trigger_count(global_entry_triggers)
    if profile_trigger_count > 0 and global_trigger_count == 0:
        logger.error(
            "[L3_CONFIG_GUARD] Ignored empty global entry_triggers override; "
            "preserving %d profile trigger condition(s)",
            profile_trigger_count,
        )
        return merged

    merged["entry_triggers"] = global_entry_triggers
    return merged
