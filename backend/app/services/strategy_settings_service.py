"""Atomic aggregate configuration service for ``/settings/strategies``.

The public document deliberately exposes only the Shadow-owned projection of
``config_type='ml'``.  Every other ML key is preserved verbatim on write.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..schemas.spot_engine_config import SpotEngineConfig
from ..schemas.strategy_settings import (
    MLShadowConfig,
    ML_SHADOW_KEYS,
    STRATEGY_SETTINGS_SCHEMA,
    STRATEGY_SETTINGS_SCHEMA_VERSION,
    StrategyConfig,
)
from .config_service import config_service


CONFIG_TYPES = ("strategy", "spot_engine", "ml")
ATR_TIMEFRAMES = ("1m", "5m", "15m", "1h")
PUBLIC_TOP_LEVEL_KEYS = {
    "schema",
    "schema_version",
    "exported_at",
    "source_hash",
    "strategy",
    "spot_engine",
    "ml_shadow",
}


class StrategySettingsConflictError(RuntimeError):
    """Raised when optimistic concurrency detects a newer saved document."""


class StrategySettingsValidationError(ValueError):
    """Raised when an imported or edited aggregate document is invalid."""


def _canonical_hash(parts: Dict[str, Any]) -> str:
    encoded = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deep_merge(current: Any, patch: Any) -> Any:
    if isinstance(current, dict) and isinstance(patch, dict):
        merged = deepcopy(current)
        for key, value in patch.items():
            merged[key] = _deep_merge(merged.get(key), value) if key in merged else deepcopy(value)
        return merged
    return deepcopy(patch)


def _reject_unknown_keys(
    payload: Dict[str, Any],
    template: Dict[str, Any],
    *,
    path: str,
    open_paths: Iterable[str] = (),
) -> None:
    open_path_set = set(open_paths)
    for key, value in payload.items():
        field_path = f"{path}.{key}" if path else key
        if key not in template:
            raise StrategySettingsValidationError(f"Unknown field: {field_path}")
        if field_path in open_path_set:
            continue
        template_value = template[key]
        if isinstance(value, dict) and isinstance(template_value, dict):
            _reject_unknown_keys(
                value,
                template_value,
                path=field_path,
                open_paths=open_path_set,
            )


def _flatten_diff(before: Any, after: Any, path: str = "") -> list[Dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[Dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else key
            if key not in before:
                changes.append({"path": child_path, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child_path, "before": before[key], "after": None})
            else:
                changes.extend(_flatten_diff(before[key], after[key], child_path))
        return changes
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


class StrategySettingsService:
    @staticmethod
    def _default_parts() -> Dict[str, Any]:
        return {
            "strategy": StrategyConfig().model_dump(mode="json"),
            "spot_engine": SpotEngineConfig().model_dump(mode="json"),
            "ml_shadow": MLShadowConfig().model_dump(mode="json"),
        }

    @staticmethod
    def _normalise_parts(raw_by_type: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        raw_strategy = raw_by_type.get("strategy") or {}
        raw_spot = raw_by_type.get("spot_engine") or {}
        raw_ml = raw_by_type.get("ml") or {}
        return {
            "strategy": StrategyConfig.model_validate(
                raw_strategy or StrategyConfig().model_dump()
            ).model_dump(mode="json"),
            "spot_engine": SpotEngineConfig.from_config_json(raw_spot).model_dump(mode="json"),
            "ml_shadow": MLShadowConfig.model_validate(
                {key: raw_ml[key] for key in ML_SHADOW_KEYS if key in raw_ml}
            ).model_dump(mode="json"),
        }

    @staticmethod
    def _bundle(parts: Dict[str, Any], *, exported_at: str | None = None) -> Dict[str, Any]:
        source_hash = _canonical_hash(parts)
        return {
            "schema": STRATEGY_SETTINGS_SCHEMA,
            "schema_version": STRATEGY_SETTINGS_SCHEMA_VERSION,
            "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
            "source_hash": source_hash,
            **deepcopy(parts),
        }

    @staticmethod
    def field_catalog() -> Dict[str, Any]:
        return {
            "strategy": StrategyConfig.model_json_schema(),
            "spot_engine": SpotEngineConfig.model_json_schema(),
            "ml_shadow": MLShadowConfig.model_json_schema(),
            "supported_values": {
                "schema": STRATEGY_SETTINGS_SCHEMA,
                "schema_version": STRATEGY_SETTINGS_SCHEMA_VERSION,
                "shadow_barrier_modes": ["FIXED", "ATR_DYNAMIC"],
                "shadow_barrier_contract_versions": [
                    "shadow_fixed_v1",
                    "shadow_atr_dynamic_v2",
                ],
                "trailing_contract_versions": ["shadow_hwm_trailing_v1"],
                "atr_timeframes": list(ATR_TIMEFRAMES),
            },
            "effects": {
                "strategy": "Ambos",
                "spot_engine": "Spot real",
                "spot_engine.shadow": "Shadow",
                "spot_engine.sell_flow.trailing": "Ambos",
                "ml_shadow": "Shadow",
            },
        }

    async def _profiles(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        lock: bool = False,
    ) -> Dict[str, ConfigProfile]:
        query = select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id.is_(None),
            ConfigProfile.config_type.in_(CONFIG_TYPES),
        )
        if lock:
            query = query.with_for_update()
        result = await db.execute(query)
        return {row.config_type: row for row in result.scalars().all()}

    async def get_config(self, db: AsyncSession, user_id: UUID) -> Dict[str, Any]:
        profiles = await self._profiles(db, user_id)
        parts = self._normalise_parts(
            {key: dict(profile.config_json or {}) for key, profile in profiles.items()}
        )
        return {
            "config": self._bundle(parts),
            "catalog": self.field_catalog(),
            "persisted": {config_type: config_type in profiles for config_type in CONFIG_TYPES},
        }

    def validate_payload(
        self,
        payload: Dict[str, Any],
        current_parts: Dict[str, Any],
    ) -> Dict[str, Any]:
        unknown_top = sorted(set(payload) - PUBLIC_TOP_LEVEL_KEYS)
        if unknown_top:
            raise StrategySettingsValidationError(
                f"Unknown top-level field(s): {', '.join(unknown_top)}"
            )
        if "schema" in payload and payload["schema"] != STRATEGY_SETTINGS_SCHEMA:
            raise StrategySettingsValidationError(
                f"schema must be {STRATEGY_SETTINGS_SCHEMA}"
            )
        if (
            "schema_version" in payload
            and payload["schema_version"] != STRATEGY_SETTINGS_SCHEMA_VERSION
        ):
            raise StrategySettingsValidationError(
                f"schema_version must be {STRATEGY_SETTINGS_SCHEMA_VERSION}"
            )

        patch_parts = {
            key: payload[key]
            for key in ("strategy", "spot_engine", "ml_shadow")
            if key in payload
        }
        if not patch_parts:
            raise StrategySettingsValidationError(
                "At least one of strategy, spot_engine or ml_shadow is required"
            )
        for key, value in patch_parts.items():
            if not isinstance(value, dict):
                raise StrategySettingsValidationError(f"{key} must be an object")

        templates = self._default_parts()
        if "strategy" in patch_parts:
            _reject_unknown_keys(
                patch_parts["strategy"],
                templates["strategy"],
                path="strategy",
                open_paths={"strategy.strategies"},
            )
        if "spot_engine" in patch_parts:
            _reject_unknown_keys(
                patch_parts["spot_engine"], templates["spot_engine"], path="spot_engine"
            )
        if "ml_shadow" in patch_parts:
            _reject_unknown_keys(
                patch_parts["ml_shadow"], templates["ml_shadow"], path="ml_shadow"
            )

        merged = _deep_merge(current_parts, patch_parts)
        try:
            strategy = StrategyConfig.model_validate(merged["strategy"]).model_dump(mode="json")
            spot_engine = SpotEngineConfig.model_validate(merged["spot_engine"]).model_dump(
                mode="json"
            )
            ml_shadow = MLShadowConfig.model_validate(merged["ml_shadow"]).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            raise StrategySettingsValidationError(str(exc)) from exc

        if (
            ml_shadow["shadow_barrier_mode"] == "ATR_DYNAMIC"
            and ml_shadow["shadow_atr_timeframe"] not in ATR_TIMEFRAMES
        ):
            raise StrategySettingsValidationError(
                "shadow_atr_timeframe does not have an implemented ATR source"
            )
        return {
            "strategy": strategy,
            "spot_engine": spot_engine,
            "ml_shadow": ml_shadow,
        }

    async def validate_import(
        self,
        db: AsyncSession,
        user_id: UUID,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        current = await self.get_config(db, user_id)
        current_parts = {
            key: current["config"][key]
            for key in ("strategy", "spot_engine", "ml_shadow")
        }
        candidate_parts = self.validate_payload(payload, current_parts)
        return {
            "valid": True,
            "source_hash": current["config"]["source_hash"],
            "config": self._bundle(candidate_parts),
            "diff": _flatten_diff(current_parts, candidate_parts),
            "catalog": current["catalog"],
        }

    async def apply(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        payload: Dict[str, Any],
        source_hash: str,
        change_description: str,
        source: str,
    ) -> Dict[str, Any]:
        profiles = await self._profiles(db, user_id, lock=True)
        raw_by_type = {
            key: dict(profile.config_json or {}) for key, profile in profiles.items()
        }
        current_parts = self._normalise_parts(raw_by_type)
        current_hash = _canonical_hash(current_parts)
        if source_hash != current_hash:
            await db.rollback()
            raise StrategySettingsConflictError(
                "Configuration changed after this screen was loaded; reload and review the diff"
            )

        candidate_parts = self.validate_payload(payload, current_parts)
        full_ml = dict(raw_by_type.get("ml") or {})
        full_ml.update(candidate_parts["ml_shadow"])
        documents = {
            "strategy": candidate_parts["strategy"],
            "spot_engine": candidate_parts["spot_engine"],
            "ml": full_ml,
        }
        changed_types: list[str] = []
        for config_type, new_json in documents.items():
            profile = profiles.get(config_type)
            previous_json = dict(profile.config_json or {}) if profile else None
            if previous_json == new_json:
                continue
            if profile is None:
                profile = ConfigProfile(
                    user_id=user_id,
                    pool_id=None,
                    config_type=config_type,
                    config_json=new_json,
                    is_active=True,
                )
                db.add(profile)
                await db.flush()
                profiles[config_type] = profile
            else:
                profile.config_json = new_json
            db.add(
                ConfigAuditLog(
                    config_id=profile.id,
                    changed_by=user_id,
                    previous_json=previous_json,
                    new_json=new_json,
                    change_description=f"[{source}] {change_description}",
                )
            )
            changed_types.append(config_type)

        await db.commit()
        for config_type in changed_types:
            await config_service.invalidate_cache(
                config_type, user_id, None, strict=True
            )

        readback = await self.get_config(db, user_id)
        if readback["config"]["source_hash"] != _canonical_hash(candidate_parts):
            raise RuntimeError("Post-write configuration readback did not match the saved document")
        return {
            "status": "success",
            "changed_config_types": changed_types,
            **readback,
        }


strategy_settings_service = StrategySettingsService()
