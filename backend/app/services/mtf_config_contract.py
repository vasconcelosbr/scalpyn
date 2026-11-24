"""Read-only conversion; never saves or consults another user's configuration."""

from copy import deepcopy
from ..schemas.mtf_chart import MTFChartConfig, presentation_preset, pump_flow_preset
from ..schemas.mtf_rules import CATALOG, DURATIONS, CONTEXT_FIELDS


def catalog():
    return {
        "indicators": CATALOG,
        "timeframes": list(DURATIONS),
        "context_fields": sorted(CONTEXT_FIELDS),
        "operators": [
            "=",
            "!=",
            ">",
            ">=",
            "<",
            "<=",
            "between",
            "crosses_above",
            "crosses_below",
        ],
        "logic": ["AND", "OR"],
        "parameter_scope": "MTF user only; no global indicator writes",
    }


def complete_config(raw, inherited):
    cfg = MTFChartConfig.model_validate(raw)
    if cfg.schema_version == "mtf_chart_v3":
        return cfg.model_dump()
    from .mtf_chart_service import calculation_window
    from .mtf_indicator_config import effective_indicators

    effective, _ = effective_indicators(inherited, {}, cfg)
    if not effective.get("atr", {}).get("enabled") or not effective["atr"].get(
        "period"
    ):
        raise ValueError("ATR_CONFIGURATION_REQUIRED_FOR_CONVERSION")
    output = cfg.model_dump()
    output.update(
        schema_version="mtf_chart_v3",
        zone_algorithm=cfg.schema_version,
        timeframes={"structure": "4h", "zone": "1h", "trigger": "5m"},
        indicators={},
        atr_period=None,
    )
    # Freeze the exact producer window of the inherited config, including its
    # cross-family warmup; a smaller config must not reinitialize an EMA.
    for tf in cfg.active_timeframes():
        window = calculation_window(effective, tf)
        for family, spec in CATALOG.items():
            setting = effective.get(family) or {}
            if not setting.get("enabled"):
                continue
            if any(key not in setting for key in spec["parameters"]):
                raise ValueError("INHERITED_INDICATOR_PARAMETERS_MISSING:" + family)
            params = {key: deepcopy(setting[key]) for key in spec["parameters"]}
            outputs = (
                [f"ema{p}" for p in params["periods"]]
                if family == "ema"
                else spec["outputs"]
            )
            for name in outputs:
                output["indicators"][name + "_" + tf] = dict(
                    family=family,
                    output=name,
                    parameters=params,
                    timeframe=tf,
                    enabled=True,
                    display=family in ("ema", "rsi", "adx", "atr"),
                    sample_window=window,
                )

    def group(key, ref):
        return {
            "logic": "AND",
            "conditions": [
                {
                    "id": key,
                    "label": key,
                    "enabled": True,
                    "left": {"ref": ref},
                    "operator": "=",
                    "right": {"value": True},
                }
            ],
        }

    output["frame_atr"] = {tf: "atr_" + tf for tf in cfg.active_timeframes()}
    output["rules"] = {
        "armed": group("near_support", "zone.near"),
        "trigger": group("candle_trigger", "trigger.present"),
        "vetoes": [],
        "scoring": [],
    }
    output["presentation"] = output.get("presentation") or presentation_preset()
    output["pump_flow"] = output.get("pump_flow") or pump_flow_preset()
    return MTFChartConfig.model_validate(output).model_dump()
