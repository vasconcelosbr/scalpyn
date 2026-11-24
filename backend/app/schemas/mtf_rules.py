"""MTF complete JSON: explicit indicator instances and safe declarative predicates."""

from __future__ import annotations
from typing import Literal
from math import isfinite
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Timeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]
DURATIONS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
# Only real OHLCV producers; order-flow stubs are deliberately not advertised.
CATALOG = {
    "ema": {"parameters": ["periods"], "outputs": ["ema{period}"]},
    "rsi": {"parameters": ["period"], "outputs": ["rsi"]},
    "adx": {"parameters": ["period"], "outputs": ["adx", "di_plus", "di_minus"]},
    "atr": {"parameters": ["period"], "outputs": ["atr"]},
    "macd": {
        "parameters": ["fast", "slow", "signal"],
        "outputs": [
            "macd",
            "macd_signal_line",
            "macd_histogram",
            "macd_histogram_pct",
            "macd_histogram_slope",
        ],
    },
    "bollinger": {
        "parameters": ["period", "deviation"],
        "outputs": [
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "bb_width",
            "bb_upper_distance_pct",
        ],
    },
    "stochastic": {
        "parameters": ["k", "d", "smooth"],
        "outputs": ["stoch_k", "stoch_d"],
    },
    "vwap": {"parameters": [], "outputs": ["vwap", "vwap_distance_pct"]},
    "obv": {"parameters": [], "outputs": ["obv"]},
    "zscore": {"parameters": ["lookback"], "outputs": ["zscore"]},
    "volume_spike": {"parameters": ["lookback"], "outputs": ["volume_spike"]},
    "rvol_strict": {"parameters": ["lookback"], "outputs": ["rvol_strict"]},
    "parabolic_sar": {
        "parameters": ["af_start", "af_increment", "af_max"],
        "outputs": ["psar", "psar_distance_pct"],
    },
}
CONTEXT_FIELDS = {
    "structure.trend",
    "structure.force",
    "zone.near",
    "zone.rejection",
    "zone.distance_atr",
    "trigger.present",
    "trigger.pattern",
    "volume.ratio",
    "btc.trend",
    "market.spread_pct",
    "flow.confirmation",
    "flow.asset.ratio",
    "flow.asset.buy_persistence",
    "flow.asset.state",
    "flow.asset.coverage",
} | {
    f"flow.capital.{tf}.{key}"
    for tf in DURATIONS
    for key in ("ratio", "state", "coverage", "buy_usdt", "sell_usdt", "net_usdt")
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IndicatorInstance(StrictModel):
    family: str
    output: str
    parameters: dict
    timeframe: Timeframe
    enabled: StrictBool = True
    display: StrictBool = False
    sample_window: int = Field(ge=2, le=1000)

    @model_validator(mode="after")
    def valid(self):
        if self.family not in CATALOG:
            raise ValueError("INDICATOR_NOT_SUPPORTED")
        spec = CATALOG[self.family]
        if set(self.parameters) != set(spec["parameters"]):
            raise ValueError(
                "INDICATOR_PARAMETERS_REQUIRED: " + ", ".join(spec["parameters"])
            )
        for key, value in self.parameters.items():
            values = value if key == "periods" and isinstance(value, list) else [value]
            if not values or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not isfinite(v)
                or v <= 0
                for v in values
            ):
                raise ValueError("INDICATOR_PARAMETER_POSITIVE_REQUIRED")
            if key not in ("deviation", "af_start", "af_increment", "af_max") and any(
                not isinstance(v, int) for v in values
            ):
                raise ValueError("INDICATOR_PERIOD_INTEGER_REQUIRED")
        if self.family == "ema" and not isinstance(self.parameters["periods"], list):
            raise ValueError("EMA_PERIODS_MUST_BE_LIST")
        outputs = (
            [f"ema{p}" for p in self.parameters.get("periods", [])]
            if self.family == "ema"
            else spec["outputs"]
        )
        if self.output not in outputs:
            raise ValueError("INDICATOR_OUTPUT_NOT_SUPPORTED")
        if self.family == "macd" and self.parameters["fast"] >= self.parameters["slow"]:
            raise ValueError("MACD_FAST_MUST_PRECEDE_SLOW")
        if (
            self.family == "parabolic_sar"
            and self.parameters["af_start"] > self.parameters["af_max"]
        ):
            raise ValueError("SAR_START_EXCEEDS_MAX")
        periods = [
            v
            for k, v in self.parameters.items()
            if k not in ("deviation", "af_start", "af_increment", "af_max")
        ]
        flat = [x for v in periods for x in (v if isinstance(v, list) else [v])]
        minimum = max(flat or [2])
        if self.family == "adx":
            minimum *= 2
        elif self.family in ("rsi", "rvol_strict"):
            minimum += 1
        elif self.family in ("macd", "stochastic"):
            minimum = sum(flat)
        if self.sample_window < minimum:
            raise ValueError("INDICATOR_SAMPLE_WINDOW_TOO_SHORT")
        return self


class Operand(StrictModel):
    ref: str | None = None
    value: float | str | bool | list[float] | None = None

    @model_validator(mode="after")
    def one(self):
        if (self.ref is None) == (self.value is None):
            raise ValueError("OPERAND_REQUIRES_REF_OR_VALUE")
        return self


class Condition(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    enabled: StrictBool = True
    left: Operand
    operator: Literal[
        "=", "!=", ">", ">=", "<", "<=", "between", "crosses_above", "crosses_below"
    ]
    right: Operand

    @model_validator(mode="after")
    def bounds(self):
        if self.operator == "between" and (
            self.right.ref is not None
            or not isinstance(self.right.value, list)
            or len(self.right.value) != 2
            or self.right.value[0] > self.right.value[1]
        ):
            raise ValueError("BETWEEN_REQUIRES_ORDERED_PAIR")
        if self.operator != "between" and isinstance(self.right.value, list):
            raise ValueError("LIST_ONLY_ALLOWED_FOR_BETWEEN")
        if isinstance(self.left.value, list):
            raise ValueError("LEFT_OPERAND_MUST_BE_SCALAR")
        if self.operator.startswith("crosses_") and not self.left.ref:
            raise ValueError("CROSS_REQUIRES_TIME_SERIES")
        return self


class RuleGroup(StrictModel):
    logic: Literal["AND", "OR"]
    conditions: list[Condition | RuleGroup] = Field(min_length=1, max_length=64)


class ScoreRule(StrictModel):
    id: str = Field(min_length=1)
    points: float = Field(ge=0)
    when: RuleGroup


class RuleSet(StrictModel):
    armed: RuleGroup
    trigger: RuleGroup
    vetoes: list[Condition | RuleGroup] = Field(default_factory=list, max_length=64)
    scoring: list[ScoreRule] = Field(default_factory=list, max_length=64)


class StageTimeframes(StrictModel):
    structure: Timeframe
    zone: Timeframe
    trigger: Timeframe


def conditions(node):
    if isinstance(node, Condition):
        yield node
    else:
        for child in node.conditions:
            yield from conditions(child)


def all_conditions(rules):
    for root in [
        rules.armed,
        rules.trigger,
        *rules.vetoes,
        *(s.when for s in rules.scoring),
    ]:
        yield from conditions(root)


def rule_uses_flow(rules):
    return bool(
        rules
        and any(
            o.ref and o.ref.startswith("flow.")
            for c in all_conditions(rules)
            if c.enabled
            for o in (c.left, c.right)
        )
    )


def operand_kind(operand):
    if operand.ref:
        ref = operand.ref
        if ref in ("structure.force", "zone.near", "zone.rejection", "trigger.present"):
            return "boolean"
        if ref in (
            "structure.trend",
            "trigger.pattern",
            "btc.trend",
            "flow.confirmation",
        ) or ref.endswith(".state"):
            return "string"
        return "number"
    if isinstance(operand.value, bool):
        return "boolean"
    if isinstance(operand.value, str):
        return "string"
    return "number"


def validate_condition_types(condition):
    left, right = operand_kind(condition.left), operand_kind(condition.right)
    if left != right:
        raise ValueError("CONDITION_OPERAND_TYPES_DIFFER:" + condition.id)
    if condition.operator not in ("=", "!=") and left != "number":
        raise ValueError("NUMERIC_OPERATOR_REQUIRES_NUMBERS:" + condition.id)
