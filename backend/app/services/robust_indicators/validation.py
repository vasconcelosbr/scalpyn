"""Validation engine for ``IndicatorEnvelope`` collections.

Five rules from the Phase 1 spec, all CRITICAL:

  1. ``volume_delta_bucket_exclusivity`` — ``volume_delta`` must come from a
     primary flow source (not a candle approximation) AND the buy/sell taker
     buckets must be mutually exclusive: ``taker_buy_volume +
     taker_sell_volume`` may not exceed total base volume by more than 5 %,
     and ``volume_delta`` (when present) must equal
     ``taker_buy_volume - taker_sell_volume`` within the same 5 % tolerance.
  2. ``critical_no_data`` — the critical indicator set
     (``rsi``, ``adx``, ``macd``, ``ema50``) must not be ``NO_DATA``.
  3. ``flow_primary_source`` — flow indicators (``taker_ratio``,
     ``buy_pressure``, ``volume_delta``, ``taker_buy_volume``,
     ``taker_sell_volume``) must come from a primary flow source.
  4. ``derived_dependencies`` — derived indicators must have their
     contributing inputs in a usable state (e.g. ``macd_histogram`` needs
     ``macd`` and ``macd_signal_line`` to be present and usable).
  5. ``sufficient_candles`` — long-period series indicators (``ema200``,
     ``adx``, ``rsi``) must not be ``NO_DATA`` due to warm-up gaps.

CRITICAL violations populate ``errors`` and force ``passed=False``.
WARNING violations populate ``warnings`` only — none of the five rules
above are WARNING; the severity is retained on the dataclass for future
extension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .envelope import DataSource, IndicatorEnvelope, IndicatorStatus


CRITICAL_INDICATORS: tuple[str, ...] = ("rsi", "adx", "macd", "ema50")

FLOW_INDICATORS: tuple[str, ...] = (
    "taker_ratio",
    "buy_pressure",
    "volume_delta",
    "taker_buy_volume",
    "taker_sell_volume",
)

FLOW_PRIMARY_SOURCES: frozenset[DataSource] = frozenset({
    DataSource.GATE_TRADES,
    DataSource.BINANCE_TRADES,
    DataSource.MERGED,
})

LONG_WARMUP_INDICATORS: tuple[str, ...] = ("ema200", "adx", "rsi", "macd_histogram")

DERIVED_DEPENDENCIES: Dict[str, tuple[str, ...]] = {
    "macd_histogram": ("macd", "macd_signal_line"),
    "macd_histogram_pct": ("macd_histogram",),
    "ema9_gt_ema50": ("ema9", "ema50"),
    "ema50_gt_ema200": ("ema50", "ema200"),
    "ema_full_alignment": ("ema9", "ema50", "ema200"),
    "atr_pct": ("atr",),
}

# Bucket exclusivity tolerance — taker_buy + taker_sell may legitimately
# differ from total base volume because of timestamp-window edge effects;
# allow 5 % slack before flagging as a CRITICAL bucket overlap.
_BUCKET_EXCLUSIVITY_TOLERANCE = 0.05


@dataclass
class ValidationRule:
    """Single rule outcome."""

    name: str
    severity: str  # "CRITICAL" | "WARNING"
    passed: bool
    message: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "severity": self.severity,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass
class ValidationResult:
    """Aggregated validation outcome."""

    passed: bool
    rules: List[ValidationRule] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "rules": [r.to_dict() for r in self.rules],
        }


def _is_present(env: IndicatorEnvelope | None) -> bool:
    return env is not None and env.status is not IndicatorStatus.NO_DATA


def _is_usable(env: IndicatorEnvelope | None) -> bool:
    return env is not None and env.is_usable


def _num(env: IndicatorEnvelope | None) -> Optional[float]:
    if env is None or not env.is_usable or env.value is None:
        return None
    try:
        return float(env.value)
    except (TypeError, ValueError):
        return None


def _check_bucket_exclusivity(
    envelopes: Mapping[str, IndicatorEnvelope],
) -> tuple[bool, str]:
    """Return ``(passed, message)`` for the buy/sell bucket exclusivity check.

    The check is skipped (and treated as passing) when we don't have at
    least the two bucket envelopes — there's nothing to double-count.
    """
    buy = _num(envelopes.get("taker_buy_volume"))
    sell = _num(envelopes.get("taker_sell_volume"))
    if buy is None or sell is None:
        return True, "buy/sell bucket envelopes absent — exclusivity N/A"

    if buy < 0 or sell < 0:
        return False, (
            f"taker_buy_volume={buy} or taker_sell_volume={sell} is negative"
        )

    base_vol = (
        _num(envelopes.get("volume_24h_base"))
        or _num(envelopes.get("base_volume"))
        or _num(envelopes.get("volume"))
    )
    bucket_sum = buy + sell
    if base_vol is not None and base_vol > 0:
        ratio = bucket_sum / base_vol
        if ratio > 1.0 + _BUCKET_EXCLUSIVITY_TOLERANCE:
            return False, (
                f"taker_buy+taker_sell={bucket_sum:.4f} exceeds base volume "
                f"{base_vol:.4f} by {(ratio - 1) * 100:.1f}% (>5% tolerance) — "
                f"buckets are not exclusive"
            )

    delta = _num(envelopes.get("volume_delta"))
    if delta is not None:
        expected = buy - sell
        scale = max(abs(expected), abs(delta), 1e-9)
        if abs(delta - expected) / scale > _BUCKET_EXCLUSIVITY_TOLERANCE:
            return False, (
                f"volume_delta={delta:.4f} ≠ taker_buy-taker_sell="
                f"{expected:.4f} (>5% mismatch — buckets inconsistent)"
            )

    return True, (
        f"buckets exclusive (buy={buy:.4f}, sell={sell:.4f}, "
        f"sum/base={'n/a' if not base_vol else f'{bucket_sum / base_vol:.3f}'})"
    )


def validate_indicator_integrity(
    envelopes: Mapping[str, IndicatorEnvelope],
) -> ValidationResult:
    """Run all five validation rules and aggregate the result."""
    rules: List[ValidationRule] = []
    errors: List[str] = []
    warnings: List[str] = []

    # ── Rule 1: volume_delta bucket exclusivity ─────────────────────────────
    # Two-part check: (a) source must be a primary flow source, (b)
    # taker buy/sell buckets must be mutually exclusive when both present.
    vd = envelopes.get("volume_delta")
    rule1_failures: list[str] = []
    if vd is not None and vd.is_usable and vd.source not in FLOW_PRIMARY_SOURCES:
        rule1_failures.append(
            f"volume_delta from non-flow source {vd.source.value} — "
            "candle-derived approximation is not allowed"
        )
    bucket_ok, bucket_msg = _check_bucket_exclusivity(envelopes)
    if not bucket_ok:
        rule1_failures.append(bucket_msg)

    if rule1_failures:
        msg = " | ".join(rule1_failures)
        rules.append(ValidationRule(
            name="volume_delta_bucket_exclusivity",
            severity="CRITICAL", passed=False, message=msg,
        ))
        errors.append(msg)
    else:
        rules.append(ValidationRule(
            name="volume_delta_bucket_exclusivity",
            severity="CRITICAL", passed=True,
            message=bucket_msg if bucket_ok else "ok",
        ))

    # ── Rule 2: critical NO_DATA ────────────────────────────────────────────
    missing_critical = [
        name for name in CRITICAL_INDICATORS
        if not _is_usable(envelopes.get(name))
    ]
    if missing_critical:
        msg = (
            f"critical indicator(s) NO_DATA: {sorted(missing_critical)}"
        )
        rules.append(ValidationRule(
            name="critical_no_data",
            severity="CRITICAL", passed=False, message=msg,
        ))
        errors.append(msg)
    else:
        rules.append(ValidationRule(
            name="critical_no_data",
            severity="CRITICAL", passed=True,
            message=f"all critical indicators present: {list(CRITICAL_INDICATORS)}",
        ))

    # ── Rule 3: flow primary source ─────────────────────────────────────────
    bad_flow = []
    for name in FLOW_INDICATORS:
        env = envelopes.get(name)
        if env is None or env.status is IndicatorStatus.NO_DATA:
            continue
        if env.source not in FLOW_PRIMARY_SOURCES:
            bad_flow.append((name, env.source.value))
    if bad_flow:
        msg = (
            "flow indicator(s) from non-flow source: "
            + ", ".join(f"{n}={s}" for n, s in bad_flow)
        )
        rules.append(ValidationRule(
            name="flow_primary_source",
            severity="CRITICAL", passed=False, message=msg,
        ))
        errors.append(msg)
    else:
        rules.append(ValidationRule(
            name="flow_primary_source",
            severity="CRITICAL", passed=True,
            message="all flow indicators come from a primary flow source",
        ))

    # ── Rule 4: derived dependencies (CRITICAL per spec) ────────────────────
    bad_derived = []
    for derived, deps in DERIVED_DEPENDENCIES.items():
        env = envelopes.get(derived)
        if env is None or env.status is IndicatorStatus.NO_DATA:
            continue
        missing_deps = [d for d in deps if not _is_usable(envelopes.get(d))]
        if missing_deps:
            bad_derived.append((derived, missing_deps))
    if bad_derived:
        msg = (
            "derived indicator(s) missing inputs: "
            + ", ".join(f"{d}<-{deps}" for d, deps in bad_derived)
        )
        rules.append(ValidationRule(
            name="derived_dependencies",
            severity="CRITICAL", passed=False, message=msg,
        ))
        errors.append(msg)
    else:
        rules.append(ValidationRule(
            name="derived_dependencies",
            severity="CRITICAL", passed=True,
            message="all derived indicator dependencies satisfied",
        ))

    # ── Rule 5: sufficient candles (CRITICAL per spec) ──────────────────────
    warmup_missing = [
        name for name in LONG_WARMUP_INDICATORS
        if envelopes.get(name) is not None
        and envelopes[name].status is IndicatorStatus.NO_DATA
    ]
    if warmup_missing:
        msg = (
            "long warm-up indicator(s) NO_DATA "
            f"(insufficient candles): {sorted(warmup_missing)}"
        )
        rules.append(ValidationRule(
            name="sufficient_candles",
            severity="CRITICAL", passed=False, message=msg,
        ))
        errors.append(msg)
    else:
        rules.append(ValidationRule(
            name="sufficient_candles",
            severity="CRITICAL", passed=True,
            message="warm-up indicator coverage OK",
        ))

    passed = not errors
    return ValidationResult(
        passed=passed, rules=rules, errors=errors, warnings=warnings,
    )


__all__ = [
    "CRITICAL_INDICATORS",
    "FLOW_INDICATORS",
    "FLOW_PRIMARY_SOURCES",
    "LONG_WARMUP_INDICATORS",
    "DERIVED_DEPENDENCIES",
    "ValidationResult",
    "ValidationRule",
    "validate_indicator_integrity",
]
