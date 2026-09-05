"""Atomic aggregate configuration service for ``/settings/strategies``.

The public document deliberately exposes only the Shadow-owned projection of
``config_type='ml'``.  Every other ML key is preserved verbatim on write.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.profile import Profile
from ..schemas.spot_engine_config import MultiLayerExecutionConfig, SpotEngineConfig
from ..schemas.strategy_settings import (
    MLShadowConfig,
    ML_SHADOW_KEYS,
    ML_SHADOW_OPTIONAL_KEYS,
    STRATEGY_SETTINGS_SCHEMA,
    STRATEGY_SETTINGS_SCHEMA_VERSION,
    StrategyConfig,
)
from .config_service import config_service
from .l3_gate_runtime_policy import (
    DEFAULT_POLICY as L3_GATE_DEFAULT_POLICY,
    POLICY_FIELDS as L3_GATE_POLICY_FIELDS,
    build_policy_snapshot,
)
from .multilayer_contract import (
    require_prepared_multilayer_config,
    require_shadow_multilayer_config,
)
from .profile_runtime_config import canonical_hash, canonical_profile_config_hash


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


def _ml_shadow_dump(model: MLShadowConfig) -> Dict[str, Any]:
    payload = model.model_dump(mode="json")
    if payload.get("canary_minimum_outcomes") is None:
        payload.pop("canary_minimum_outcomes", None)
    return payload


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
    def _assert_coverage_envelope(
        payload: Dict[str, Any],
        *,
        symbol: str,
        timeframe: str,
        scheduler_group: str,
        layer_config: Dict[str, Any],
        now: datetime,
    ) -> Dict[str, Any]:
        """Validate a latest producer row before observation can be enabled."""

        policy = ((layer_config.get("source_policies") or {}).get("ohlcv") or {})
        allowed = {str(value) for value in policy.get("allowed_source_providers") or []}
        expected_policy = str(policy.get("provider_policy_id") or "")
        margin = layer_config.get("validity_margin_seconds")
        if margin is None:
            raise StrategySettingsValidationError(
                f"{timeframe}_{symbol}_VALIDITY_MARGIN_CONFIG_REQUIRED"
            )
        duration_seconds = {"1h": 3600, "15m": 900, "5m": 300}[timeframe]
        envelopes = [value for value in payload.values() if isinstance(value, dict)]
        if not envelopes:
            raise StrategySettingsValidationError(
                f"{timeframe}_{symbol}_{scheduler_group}_WARMUP_INCOMPLETE"
            )
        source_timestamps: set[datetime] = set()
        config_hashes: set[str] = set()
        producer_versions: set[str] = set()
        for original in envelopes:
            envelope = dict(original)
            expected_hash = envelope.pop("envelope_hash", None)
            if not expected_hash or expected_hash != canonical_hash(envelope):
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_ENVELOPE_HASH_INVALID"
                )
            if (
                envelope.get("timeframe") != timeframe
                or envelope.get("market_type") != "spot"
                or envelope.get("scheduler_group") != scheduler_group
            ):
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_TEMPORAL_IDENTITY_INVALID"
                )
            if envelope.get("candle_policy") != "CLOSED_ONLY" or envelope.get("candle_closed") is not True:
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_OPEN_CANDLE_REJECTED"
                )
            if str(envelope.get("source_provider")) not in allowed:
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_SOURCE_PROVIDER_REJECTED"
                )
            if str(envelope.get("provider_policy_id") or "") != expected_policy:
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_PROVIDER_POLICY_REJECTED"
                )
            try:
                source_timestamp = datetime.fromisoformat(
                    str(envelope["source_timestamp"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise StrategySettingsValidationError(
                    f"{timeframe}_{symbol}_{scheduler_group}_SOURCE_TIMESTAMP_INVALID"
                ) from exc
            if source_timestamp.tzinfo is None:
                source_timestamp = source_timestamp.replace(tzinfo=timezone.utc)
            else:
                source_timestamp = source_timestamp.astimezone(timezone.utc)
            source_timestamps.add(source_timestamp)
            config_hashes.add(str(envelope.get("config_hash") or ""))
            producer_versions.add(str(envelope.get("producer_version") or ""))
        if len(source_timestamps) != 1 or len(config_hashes) != 1 or "" in config_hashes:
            raise StrategySettingsValidationError(
                f"{timeframe}_{symbol}_{scheduler_group}_IDENTITY_CONFLICT"
            )
        source_timestamp = next(iter(source_timestamps))
        if source_timestamp > now - timedelta(seconds=duration_seconds):
            raise StrategySettingsValidationError(
                f"{timeframe}_{symbol}_{scheduler_group}_OPEN_CANDLE_REJECTED"
            )
        expires_at = source_timestamp + timedelta(
            seconds=duration_seconds + int(margin)
        )
        if now > expires_at:
            raise StrategySettingsValidationError(
                f"{timeframe}_{symbol}_{scheduler_group}_CONTEXT_EXPIRED"
            )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "scheduler_group": scheduler_group,
            "source_timestamp": source_timestamp.isoformat(),
            "expires_at": expires_at.isoformat(),
            "config_hash": next(iter(config_hashes)),
            "producer_versions": sorted(producer_versions),
        }

    async def _assert_multilayer_runtime_ready(
        self,
        db: AsyncSession,
        user_id: UUID,
        contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Require complete, fresh, closed-only producer coverage for active Spot symbols."""

        symbols = [str(row.symbol) for row in (await db.execute(text("""
            SELECT DISTINCT pc.symbol
              FROM pool_coins pc
              JOIN pools p ON p.id = pc.pool_id
             WHERE p.user_id = :user_id
               AND p.is_active IS TRUE
               AND p.market_type = 'spot'
               AND pc.is_active IS TRUE
               AND pc.market_type = 'spot'
             ORDER BY pc.symbol
        """), {"user_id": str(user_id)})).fetchall()]
        if not symbols:
            raise StrategySettingsValidationError("MTF_ACTIVE_SPOT_SYMBOLS_REQUIRED")
        rows = (await db.execute(text("""
            SELECT DISTINCT ON (symbol, timeframe, scheduler_group)
                   symbol, timeframe, scheduler_group, indicators_json
              FROM indicators
             WHERE market_type = 'spot'
               AND symbol = ANY(CAST(:symbols AS TEXT[]))
               AND timeframe IN ('1h', '15m', '5m')
               AND scheduler_group IN ('structural', 'microstructure')
             ORDER BY symbol, timeframe, scheduler_group, time DESC
        """), {"symbols": symbols})).mappings().all()
        by_identity = {
            (str(row["symbol"]), str(row["timeframe"]), str(row["scheduler_group"])):
            dict(row["indicators_json"] or {})
            for row in rows
        }
        requirements = (
            ("L1", "1h", "structural"),
            ("L2", "15m", "structural"),
            ("L3", "5m", "structural"),
            ("L3", "5m", "microstructure"),
        )
        now = datetime.now(timezone.utc)
        evidence: list[Dict[str, Any]] = []
        missing: list[str] = []
        for symbol in symbols:
            for layer, timeframe, scheduler_group in requirements:
                payload = by_identity.get((symbol, timeframe, scheduler_group))
                if payload is None:
                    missing.append(f"{symbol}:{timeframe}:{scheduler_group}")
                    continue
                evidence.append(self._assert_coverage_envelope(
                    payload,
                    symbol=symbol,
                    timeframe=timeframe,
                    scheduler_group=scheduler_group,
                    layer_config=contract["layers"][layer],
                    now=now,
                ))
        if missing:
            raise StrategySettingsValidationError(
                "MTF_RUNTIME_COVERAGE_NOT_READY:" + ",".join(missing)
            )
        return {
            "active_spot_symbols": len(symbols),
            "required_rows": len(symbols) * len(requirements),
            "validated_rows": len(evidence),
            "identities": evidence,
        }

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
            "ml_shadow": _ml_shadow_dump(
                MLShadowConfig.model_validate(
                    {
                        key: raw_ml[key]
                        for key in (*ML_SHADOW_KEYS, *ML_SHADOW_OPTIONAL_KEYS)
                        if key in raw_ml
                    }
                )
            ),
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
                    "shadow_atr_dynamic_v3",
                ],
                "shadow_barrier_geometry_policies": [
                    "LEGACY_INDEPENDENT_CLAMP",
                    "SL_ANCHORED_RATIO",
                    "ATR_CLAMPED_BEFORE_MULTIPLY",
                ],
                "trailing_contract_versions": ["shadow_hwm_trailing_v1"],
                "shadow_trailing_contract_versions": [
                    "shadow_hwm_trailing_v1",
                    "shadow_trailing_policy_v2",
                ],
                "shadow_trailing_policy_families": ["FIXED", "STEPPED", "PROPORTIONAL"],
                "atr_timeframes": list(ATR_TIMEFRAMES),
            },
            "effects": {
                "strategy": "Ambos",
                "spot_engine": "Spot real",
                "spot_engine.shadow": "Shadow",
                "spot_engine.sell_flow.trailing": "Ambos (enabled/never_sell_at_loss/min_profit_pct); Ambos ate' shadow_trailing_contract_version=v2, quando os campos numericos de trilha em ml_shadow passam a valer so' para o Shadow",
                "ml_shadow": "Shadow",
                "ml_shadow.shadow_trailing_*": "Shadow apenas (nunca afeta a venda do spot ao vivo)",
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
            spot_engine_model = SpotEngineConfig.model_validate(merged["spot_engine"])
            spot_engine = spot_engine_model.model_dump(mode="json")
            ml_shadow = _ml_shadow_dump(
                MLShadowConfig.model_validate(merged["ml_shadow"])
            )
        except ValidationError as exc:
            raise StrategySettingsValidationError(str(exc)) from exc

        multilayer = spot_engine_model.scanner.multilayer_contract
        current_multilayer = (
            (((current_parts.get("spot_engine") or {}).get("scanner") or {})
             .get("multilayer_contract"))
            or MultiLayerExecutionConfig().model_dump(mode="json")
        )
        candidate_multilayer = multilayer.model_dump(mode="json")
        if candidate_multilayer != current_multilayer:
            if not current_multilayer.get("enabled") and candidate_multilayer.get("enabled"):
                raise StrategySettingsValidationError(
                    "R6 multilayer authority must remain disabled; use the dedicated activation endpoint"
                )
            newly_enabled_layers = sorted(
                layer
                for layer, layer_config in multilayer.layers.items()
                if layer_config.observational_enabled
                and not (((current_multilayer.get("layers") or {}).get(layer) or {})
                         .get("observational_enabled"))
            )
            if newly_enabled_layers:
                raise StrategySettingsValidationError(
                    "R6 observational layers must remain disabled: "
                    + ", ".join(newly_enabled_layers)
                    + "; use the dedicated activation endpoint"
                )
            raise StrategySettingsValidationError(
                "MULTILAYER_CONTRACT_REQUIRES_DEDICATED_ENDPOINT"
            )
        if multilayer.enabled:
            try:
                require_shadow_multilayer_config({
                    "multilayer_contract": candidate_multilayer
                })
            except ValueError as exc:
                raise StrategySettingsValidationError(str(exc)) from exc
        else:
            enabled_layers = sorted(
                layer
                for layer, layer_config in multilayer.layers.items()
                if layer_config.observational_enabled
            )
            if enabled_layers:
                raise StrategySettingsValidationError(
                    "R6 observational layers must remain disabled: "
                    + ", ".join(enabled_layers)
                )
            if multilayer.decision_feature_valid_from is not None:
                raise StrategySettingsValidationError(
                    "R6 decision feature boundary is defined but cannot be applied"
                )

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

    async def materialize_l3_gate_policy(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Idempotently persist only the governed L3 runtime controls."""

        profiles = await self._profiles(db, user_id, lock=apply)
        profile = profiles.get("spot_engine")
        before_json = dict(profile.config_json or {}) if profile else {}
        candidate = deepcopy(before_json)
        scanner = dict(candidate.get("scanner") or {})
        for field in L3_GATE_POLICY_FIELDS:
            scanner.setdefault(field, deepcopy(L3_GATE_DEFAULT_POLICY[field]))
        candidate["scanner"] = scanner
        # Validate the full runtime document, but retain its original shape so
        # this backfill cannot materialize unrelated defaults.
        SpotEngineConfig.from_config_json(candidate)
        changed = candidate != before_json
        policy = build_policy_snapshot(scanner)

        if not apply or not changed:
            if apply:
                await db.rollback()
            return {
                "user_id": str(user_id),
                "changed": changed,
                "applied": False,
                "runtime_policy": policy,
            }

        if profile is None:
            profile = ConfigProfile(
                user_id=user_id,
                pool_id=None,
                config_type="spot_engine",
                config_json=candidate,
                is_active=True,
            )
            db.add(profile)
            await db.flush()
        else:
            profile.config_json = candidate
        db.add(
            ConfigAuditLog(
                config_id=profile.id,
                changed_by=user_id,
                previous_json=before_json or None,
                new_json=candidate,
                change_description="[P1_L3_GATE] materialize governed runtime controls",
            )
        )
        await db.commit()
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
        readback = await self.get_config(db, user_id)
        readback_scanner = readback["config"]["spot_engine"]["scanner"]
        readback_policy = build_policy_snapshot(readback_scanner)
        if readback_policy["config_hash"] != policy["config_hash"]:
            raise RuntimeError("L3 gate policy readback hash mismatch")
        return {
            "user_id": str(user_id),
            "changed": True,
            "applied": True,
            "runtime_policy": readback_policy,
        }

    async def materialize_multilayer_contract(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        layer_profile_ids: Dict[str, UUID],
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Persist the R6 contract while keeping every layer non-operational."""

        if set(layer_profile_ids) != {"L1", "L2"}:
            raise StrategySettingsValidationError(
                "layer_profile_ids must contain exactly L1 and L2"
            )
        profiles = await self._profiles(db, user_id, lock=apply)
        profile = profiles.get("spot_engine")
        before_json = dict(profile.config_json or {}) if profile else {}
        candidate = deepcopy(before_json)
        scanner = dict(candidate.get("scanner") or {})
        existing = scanner.get("multilayer_contract")
        if existing:
            contract = deepcopy(existing)
        else:
            contract = MultiLayerExecutionConfig().model_dump(mode="json")
            prepared_at = datetime.now(timezone.utc).isoformat()
            contract["execution_contract_valid_from"] = prepared_at
            contract["consolidation_valid_from"] = prepared_at
            contract["decision_feature_valid_from"] = None
            legacy_policies = (
                (scanner.get("l3_global_block_range_compiler") or {}).get(
                    "source_policies"
                )
                or {}
            )
            if legacy_policies:
                contract["layers"]["L3"]["source_policies"] = deepcopy(
                    legacy_policies
                )

        contract["enabled"] = False
        for layer in ("L1", "L2", "L3"):
            contract["layers"][layer]["observational_enabled"] = False
        contract["layers"]["L1"]["profile_id"] = str(layer_profile_ids["L1"])
        contract["layers"]["L2"]["profile_id"] = str(layer_profile_ids["L2"])
        contract["layers"]["L3"]["profile_id"] = None
        scanner["multilayer_contract"] = contract
        candidate["scanner"] = scanner

        SpotEngineConfig.from_config_json(candidate)
        prepared = require_prepared_multilayer_config(scanner)
        changed = candidate != before_json
        contract_hash = _canonical_hash(prepared)
        if not apply or not changed:
            if apply:
                await db.rollback()
            return {
                "user_id": str(user_id),
                "changed": changed,
                "applied": False,
                "contract_hash": contract_hash,
                "multilayer_contract": prepared,
            }

        if profile is None:
            profile = ConfigProfile(
                user_id=user_id,
                pool_id=None,
                config_type="spot_engine",
                config_json=candidate,
                is_active=True,
            )
            db.add(profile)
            await db.flush()
        else:
            profile.config_json = candidate
        db.add(
            ConfigAuditLog(
                config_id=profile.id,
                changed_by=user_id,
                previous_json=before_json or None,
                new_json=candidate,
                change_description=(
                    "[R6_MULTILAYER_CONTRACT] materialize disabled layer contracts"
                ),
            )
        )
        await db.commit()
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
        readback = await self.get_config(db, user_id)
        readback_contract = require_prepared_multilayer_config(
            readback["config"]["spot_engine"]["scanner"]
        )
        if _canonical_hash(readback_contract) != contract_hash:
            raise RuntimeError("R6 multilayer contract readback hash mismatch")
        return {
            "user_id": str(user_id),
            "changed": True,
            "applied": True,
            "contract_hash": contract_hash,
            "multilayer_contract": readback_contract,
            "source_hash": readback["config"]["source_hash"],
        }

    async def activate_multilayer_shadow(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        layer_profile_ids: Dict[str, UUID],
        l3_source_identity: Dict[str, Any],
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Bind immutable calibrated profiles and enable observation only."""
        if set(layer_profile_ids) != {"L1", "L2"}:
            raise StrategySettingsValidationError(
                "layer_profile_ids must contain exactly L1 and L2"
            )
        rows = (await db.execute(select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(list(layer_profile_ids.values())),
        ).with_for_update())).scalars().all()
        by_id = {row.id: row for row in rows}
        bindings: Dict[str, Dict[str, Any]] = {}
        for layer, profile_id in layer_profile_ids.items():
            profile = by_id.get(profile_id)
            if profile is None:
                raise StrategySettingsValidationError(f"{layer}_PROFILE_NOT_FOUND")
            config = dict(profile.config or {})
            mtf = config.get("mtf_layer") or {}
            calibration = config.get("calibration") or {}
            if (
                profile.profile_type != "MTF_LAYER"
                or not profile.is_shadow_only
                or profile.live_trading_enabled
                or not profile.is_active
                or mtf.get("layer") != layer
                or mtf.get("activation_mode") != "SHADOW"
                or calibration.get("status") != "PASSED"
                or calibration.get("method") != "WALK_FORWARD"
                or calibration.get("baseline_outperformed") is not True
                or calibration.get("worst_fold_drawdown_not_worse") is not True
                or calibration.get("min_samples") is None
            ):
                raise StrategySettingsValidationError(
                    f"{layer}_PROFILE_SHADOW_GATE_FAILED"
                )
            version = (await db.execute(text("""
                SELECT id, config_hash
                  FROM profile_versions
                 WHERE profile_id = CAST(:profile_id AS UUID)
                   AND status = 'SHADOW'
                 ORDER BY version_number DESC, created_at DESC
                 LIMIT 1
            """), {"profile_id": str(profile_id)})).mappings().one_or_none()
            config_hash = canonical_profile_config_hash(config)
            if version is None or version["config_hash"] != config_hash:
                raise StrategySettingsValidationError(
                    f"{layer}_PROFILE_VERSION_HASH_INVALID"
                )
            bindings[layer] = {
                "profile_id": str(profile_id),
                "profile_version_id": str(version["id"]),
                "profile_config_hash": config_hash,
                "source_identity": config.get("source_identity") or {},
            }

        profiles = await self._profiles(db, user_id, lock=apply)
        target = profiles.get("spot_engine")
        before_json = dict(target.config_json or {}) if target else {}
        candidate = deepcopy(before_json)
        scanner = dict(candidate.get("scanner") or {})
        contract = deepcopy(scanner.get("multilayer_contract") or {})
        if not contract:
            raise StrategySettingsValidationError("MULTILAYER_CONTRACT_NOT_MATERIALIZED")
        now = datetime.now(timezone.utc).isoformat()
        contract.update({
            "enabled": True,
            "activation_mode": "SHADOW",
            "operational_effect": False,
            "decision_feature_contract_version": "multilayer_decision_context_v2",
            "decision_feature_valid_from": now,
        })
        expected_tf = {"L1": "1h", "L2": "15m"}
        for layer in ("L1", "L2"):
            source = bindings[layer]["source_identity"]
            contract["layers"][layer].update({
                "observational_enabled": True,
                "profile_id": bindings[layer]["profile_id"],
                "profile_version_id": bindings[layer]["profile_version_id"],
                "profile_config_hash": bindings[layer]["profile_config_hash"],
                "default_timeframe": expected_tf[layer],
                "validity_margin_seconds": source.get("validity_margin_seconds"),
                "source_policies": {"ohlcv": {
                    "allowed_source_providers": source.get("allowed_source_providers") or [],
                    "provider_policy_id": source.get("provider_policy_id"),
                    "timeframe": expected_tf[layer],
                    "candle_policy": source.get("candle_policy"),
                }},
            })
        contract["layers"]["L3"].update({
            "observational_enabled": True,
            "default_timeframe": "5m",
            "validity_margin_seconds": l3_source_identity.get("validity_margin_seconds"),
            "source_policies": {"ohlcv": {
                "allowed_source_providers": l3_source_identity.get("allowed_source_providers") or [],
                "provider_policy_id": l3_source_identity.get("provider_policy_id"),
                "timeframe": "5m",
                "candle_policy": l3_source_identity.get("candle_policy"),
            }},
        })
        scanner["multilayer_contract"] = contract
        candidate["scanner"] = scanner
        SpotEngineConfig.from_config_json(candidate)
        validated = require_shadow_multilayer_config(scanner)
        coverage = await self._assert_multilayer_runtime_ready(
            db, user_id, validated
        )
        contract_hash = _canonical_hash(validated)
        if not apply:
            await db.rollback()
            return {
                "changed": candidate != before_json,
                "applied": False,
                "contract_hash": contract_hash,
                "multilayer_contract": validated,
                "coverage": coverage,
            }
        if target is None:
            raise StrategySettingsValidationError("SPOT_ENGINE_CONFIG_REQUIRED")
        target.config_json = candidate
        db.add(ConfigAuditLog(
            config_id=target.id,
            changed_by=user_id,
            previous_json=before_json,
            new_json=candidate,
            change_description=(
                "[MTF_SPOT_SHADOW] activate observational contexts; "
                "operational_effect=false"
            ),
        ))
        await db.commit()
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
        return {
            "changed": True,
            "applied": True,
            "contract_hash": contract_hash,
            "multilayer_contract": validated,
            "coverage": coverage,
        }

    async def disable_multilayer_shadow(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Idempotent configuration rollback; historical observations are preserved."""

        profiles = await self._profiles(db, user_id, lock=apply)
        target = profiles.get("spot_engine")
        if target is None:
            raise StrategySettingsValidationError("SPOT_ENGINE_CONFIG_REQUIRED")
        before_json = dict(target.config_json or {})
        candidate = deepcopy(before_json)
        scanner = dict(candidate.get("scanner") or {})
        contract = deepcopy(scanner.get("multilayer_contract") or {})
        if not contract:
            raise StrategySettingsValidationError("MULTILAYER_CONTRACT_NOT_MATERIALIZED")
        contract.update({
            "enabled": False,
            "activation_mode": "DRAFT",
            "operational_effect": False,
            "decision_feature_valid_from": None,
        })
        for layer in ("L1", "L2", "L3"):
            contract["layers"][layer]["observational_enabled"] = False
        scanner["multilayer_contract"] = contract
        candidate["scanner"] = scanner
        SpotEngineConfig.from_config_json(candidate)
        changed = candidate != before_json
        if not apply or not changed:
            if apply:
                await db.rollback()
            return {
                "changed": changed,
                "applied": False,
                "multilayer_contract": contract,
            }
        target.config_json = candidate
        db.add(ConfigAuditLog(
            config_id=target.id,
            changed_by=user_id,
            previous_json=before_json,
            new_json=candidate,
            change_description=(
                "[MTF_SPOT_ROLLBACK] disable observational contexts; preserve history"
            ),
        ))
        await db.commit()
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
        return {
            "changed": True,
            "applied": True,
            "multilayer_contract": contract,
        }


strategy_settings_service = StrategySettingsService()
