"""Canonical validation for executable strategy profile configuration."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict


def validate_profile_config(
    config: Dict[str, Any], *, require_feature_identity: bool = False
) -> Dict[str, Any]:
    """Validate and normalize a profile without changing economic values."""
    from .entry_risk_features import assert_no_observational_execution_fields

    source = deepcopy(config or {})
    assert_no_observational_execution_fields(source)
    # Preserve every unrelated profile field. Validation may normalize the
    # executable sections below, but it must never silently delete risk,
    # sizing, presentation or future schema fields from an existing profile.
    validated: Dict[str, Any] = deepcopy(source)
    validated["default_timeframe"] = source.get("default_timeframe", "5m")

    filters = source.get("filters", {})
    validated["filters"] = {
        "logic": filters.get("logic", "AND").upper(),
        "conditions": filters.get("conditions", []),
    }
    for condition in validated["filters"]["conditions"]:
        field = condition.get("field") or condition.get("indicator")
        if not field:
            raise ValueError("Filter condition missing 'field'")
        condition["field"] = field
        condition.pop("indicator", None)
        condition.setdefault("operator", "==")

    scoring = source.get("scoring", {})
    weights = scoring.get("weights", {})
    validated["scoring"] = {
        "enabled": scoring.get("enabled", True),
        "weights": {
            "liquidity": weights.get("liquidity", 25),
            "market_structure": weights.get("market_structure", 25),
            "momentum": weights.get("momentum", 25),
            "signal": weights.get("signal", 25),
        },
        "rules": scoring.get("rules", []),
        "selected_rule_ids": scoring.get("selected_rule_ids", []),
        "thresholds": scoring.get(
            "thresholds", {"strong_buy": 80, "buy": 65, "neutral": 40}
        ),
    }

    signals = source.get("signals", {})
    validated["signals"] = {
        "logic": signals.get("logic", "AND").upper(),
        "conditions": signals.get("conditions", []),
    }
    for condition in validated["signals"]["conditions"]:
        field = condition.get("field") or condition.get("indicator")
        if not field:
            raise ValueError("Signal condition missing 'field'")
        condition["field"] = field
        condition.pop("indicator", None)
        condition.setdefault("operator", "==")

    block_rules = source.get("block_rules", {})
    validated["block_rules"] = {"blocks": block_rules.get("blocks", [])}

    entry_triggers = source.get("entry_triggers", {})
    validated["entry_triggers"] = {
        "logic": entry_triggers.get("logic", "AND").upper(),
        "conditions": entry_triggers.get("conditions", []),
    }

    if require_feature_identity:
        from .l3_authorization_contract_v3 import validate_profile_contract

        contract_errors = validate_profile_contract(validated)
        if contract_errors:
            raise ValueError(
                "L3_FEATURE_IDENTITY_INVALID:"
                + json.dumps(contract_errors, sort_keys=True)
            )

    return validated


def profile_config_warnings(validated_config: Dict[str, Any]) -> list[str]:
    """Non-blocking advisory warnings about an already-validated profile.

    AUD-001 (auditoria shadow SL_HIT 2026-09-11): ``entry_triggers`` and
    ``signals`` can both carry conditions, but ``ProfileEngine`` only
    evaluates ``signals`` when ``entry_triggers.conditions`` is empty (see
    ``profile_engine.py``, ``self.signals_config = _et if _et.get(...)
    else _sig``). A profile with both populated silently authorizes entries
    using only ``entry_triggers`` -- ``signals`` conditions (e.g. a
    momentum_score floor) are never evaluated, with no error and no signal
    to whoever is reading the profile.

    Distinct from ``validate_profile_config``'s hard errors: never raises,
    never mutates the config. Callers append the result to whatever
    ``warnings`` list they already return to the profile editor.
    """
    warnings: list[str] = []
    entry_triggers = (validated_config.get("entry_triggers") or {}).get("conditions") or []
    signals = (validated_config.get("signals") or {}).get("conditions") or []
    if entry_triggers and signals:
        warnings.append(
            "entry_triggers e signals estão ambos preenchidos neste perfil: apenas "
            "entry_triggers é avaliado para autorizar a entrada -- signals é "
            "ignorado inteiramente enquanto entry_triggers tiver condições. "
            "Considere mover as condições de signals para entry_triggers ou "
            "esvaziar uma das duas seções."
        )
    return warnings
