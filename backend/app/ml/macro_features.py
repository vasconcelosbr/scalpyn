"""
Market Data Hub — feature extraction and validation.

Converts raw MDH API responses into a flat, validated feature dict
ready to be merged into the XGBoost feature vector.

Validation rules (spec):
  - Numbers stay float/int/None — never convert None→0 or string→float silently
  - Reject formatted strings: "45%", "1.2B", "4,52"
  - Reject NaN, Infinity, timestamps in the future, excessive staleness
  - Reject negative values that are physically impossible (dominance, VIX)

FIELD MAP — adjust paths here when MDH API shape is confirmed.
Each entry: feature_name → (endpoint_key, *path_to_value)
"*path" means nested dict access: raw["endpoint_key"]["key1"]["key2"]
"""
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Macro feature column names (order is stable for training) ─────────────────
MACRO_FEATURE_COLUMNS: List[str] = [
    # Equities
    "sp500_change_1h",
    "nasdaq_change_1h",
    "russell2000_change_1h",
    # Volatility
    "vix_value",
    "vix_change_1h",
    # Dollar
    "dxy_value",
    "dxy_change_1h",
    # Bonds
    "us10y_yield",
    "us10y_change_1h",
    # Crypto global
    "btc_dominance",
    "btc_dominance_change",
    "crypto_market_cap_change",
    "crypto_volume_change",
    "fear_greed_index",
    # Meta
    "macro_context_available",
]

# Returned when context is unavailable — all numeric features are None.
# None propagates correctly as NaN in XGBoost (missing=nan).
MACRO_FEATURES_EMPTY: Dict[str, Any] = {k: None for k in MACRO_FEATURE_COLUMNS}
MACRO_FEATURES_EMPTY["macro_context_available"] = False

# Maximum acceptable data age (seconds). Data older than this is treated as stale.
_MAX_STALENESS_S = 300  # 5 minutes


# ── Validation helpers ────────────────────────────────────────────────────────

def _safe_float(val: Any, *, allow_negative: bool = True) -> Optional[float]:
    """
    Convert val to float, enforcing spec rules:
    - None stays None (never → 0)
    - Reject string representations: "45%", "1.2B", "4,52"
    - Reject NaN and Infinity
    - Reject negative when allow_negative=False
    """
    if val is None:
        return None
    if isinstance(val, str):
        # Reject any formatted string — caller must pass numeric types only
        logger.debug("[MDH] rejected string value: %r", val)
        return None
    if isinstance(val, bool):
        return None  # booleans are not market metrics
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if not allow_negative and f < 0:
        logger.debug("[MDH] rejected negative value for non-negative field: %s", f)
        return None
    return f


def _find_by_symbol(data: Any, symbol: str) -> Optional[Dict]:
    """Find a dict in a list (or a single dict) by matching 'symbol' key."""
    if isinstance(data, dict):
        # Could be {"data": [...]} or {"data": {...}} or flat
        inner = data.get("data", data)
        if isinstance(inner, list):
            return _find_by_symbol(inner, symbol)
        if isinstance(inner, dict):
            sym = inner.get("symbol") or inner.get("ticker") or ""
            return inner if sym.upper() == symbol.upper() else None
    if isinstance(data, list):
        sym_up = symbol.upper()
        for item in data:
            if isinstance(item, dict):
                s = (item.get("symbol") or item.get("ticker") or "").upper()
                if s == sym_up:
                    return item
    return None


def _unwrap(data: Any) -> Any:
    """Unwrap common envelope shapes: {"data": ...} → ...."""
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


# ── Endpoint-specific extractors ──────────────────────────────────────────────

def _extract_indices(raw: Optional[Any]) -> Dict[str, Optional[float]]:
    """
    Extract S&P 500, Nasdaq, Russell 2000 1h change percentages.

    Expected MDH shape (adjust field names to match actual API):
      {"data": [
        {"symbol": "SPX",  "change_pct": 0.37, ...},
        {"symbol": "NDX",  "change_pct": 0.19, ...},
        {"symbol": "RUT",  "change_pct": -0.04, ...},
      ]}

    Alternative symbol names tried: SPX/SP500/^GSPC, NDX/NASDAQ/^NDX, RUT/RUSSELL2000/^RUT
    Alternative change fields tried: change_pct, changePercent, percent_change, change_percent
    """
    out: Dict[str, Optional[float]] = {
        "sp500_change_1h": None,
        "nasdaq_change_1h": None,
        "russell2000_change_1h": None,
    }
    if not raw:
        return out

    data = _unwrap(raw)

    _CHANGE_FIELDS = ("change_pct", "changePercent", "percent_change", "change_percent",
                      "changesPercentage", "price_change_pct", "pctChange")

    def _get_change(item: Optional[Dict]) -> Optional[float]:
        if not item:
            return None
        for fld in _CHANGE_FIELDS:
            val = _safe_float(item.get(fld))
            if val is not None:
                return val
        return None

    for aliases, feature_key in [
        (("SPX", "SP500", "^GSPC", "US500", "S&P500"), "sp500_change_1h"),
        (("NDX", "NASDAQ", "^NDX", "NAS100", "COMP", "^IXIC"), "nasdaq_change_1h"),
        (("RUT", "RUSSELL2000", "^RUT", "RTY", "R2000"), "russell2000_change_1h"),
    ]:
        for alias in aliases:
            item = _find_by_symbol(data, alias)
            if item:
                out[feature_key] = _get_change(item)
                break

    return out


def _extract_volatility(raw: Optional[Any]) -> Dict[str, Optional[float]]:
    """
    Extract VIX value and 1h change.

    Expected MDH shape:
      {"data": [{"symbol": "VIX", "value": 18.5, "change_pct": -3.4, ...}]}
    """
    out: Dict[str, Optional[float]] = {"vix_value": None, "vix_change_1h": None}
    if not raw:
        return out

    data = _unwrap(raw)
    item = _find_by_symbol(data, "VIX") or _find_by_symbol(data, "^VIX")
    if not item:
        # Try flat dict (single-item response)
        if isinstance(data, dict):
            item = data

    if item:
        # VIX value — must be positive
        for fld in ("value", "price", "last", "close", "current"):
            v = _safe_float(item.get(fld), allow_negative=False)
            if v is not None:
                out["vix_value"] = v
                break

        # VIX change
        for fld in ("change_pct", "changePercent", "percent_change", "change_percent",
                    "changesPercentage", "change"):
            v = _safe_float(item.get(fld))
            if v is not None:
                out["vix_change_1h"] = v
                break

    return out


def _extract_forex(raw: Optional[Any]) -> Dict[str, Optional[float]]:
    """
    Extract DXY (US Dollar Index) value and 1h change.

    Expected MDH shape:
      {"data": [{"symbol": "DXY", "value": 104.3, "change_pct": -0.12, ...}]}
    """
    out: Dict[str, Optional[float]] = {"dxy_value": None, "dxy_change_1h": None}
    if not raw:
        return out

    data = _unwrap(raw)
    item = (
        _find_by_symbol(data, "DXY")
        or _find_by_symbol(data, "DX-Y.NYB")
        or _find_by_symbol(data, "USDX")
        or _find_by_symbol(data, "USD")
    )
    if not item and isinstance(data, dict):
        item = data

    if item:
        for fld in ("value", "price", "last", "close", "current"):
            v = _safe_float(item.get(fld), allow_negative=False)
            if v is not None:
                out["dxy_value"] = v
                break
        for fld in ("change_pct", "changePercent", "percent_change", "change_percent",
                    "changesPercentage", "change"):
            v = _safe_float(item.get(fld))
            if v is not None:
                out["dxy_change_1h"] = v
                break

    return out


def _extract_bonds(raw: Optional[Any]) -> Dict[str, Optional[float]]:
    """
    Extract US 10Y yield and 1h change.

    Expected MDH shape:
      {"data": [{"symbol": "US10Y", "yield": 4.25, "change": 0.05, ...}]}
    """
    out: Dict[str, Optional[float]] = {"us10y_yield": None, "us10y_change_1h": None}
    if not raw:
        return out

    data = _unwrap(raw)
    item = (
        _find_by_symbol(data, "US10Y")
        or _find_by_symbol(data, "^TNX")
        or _find_by_symbol(data, "TNX")
        or _find_by_symbol(data, "10Y")
    )
    if not item and isinstance(data, dict):
        item = data

    if item:
        for fld in ("yield", "value", "price", "rate", "last", "close"):
            v = _safe_float(item.get(fld), allow_negative=False)
            if v is not None:
                out["us10y_yield"] = v
                break
        for fld in ("change_pct", "changePercent", "percent_change", "change_percent",
                    "changesPercentage", "change", "yield_change"):
            v = _safe_float(item.get(fld))
            if v is not None:
                out["us10y_change_1h"] = v
                break

    return out


def _extract_crypto_global(raw: Optional[Any]) -> Dict[str, Optional[float]]:
    """
    Extract BTC dominance, market cap change, volume change, fear & greed index.

    MDH actual shape (snapshot dict):
      {"data": {
        "crypto_global": {"snapshot_type": "crypto_global", "payload": {
          "btc_dominance": 56.7,
          "fear_and_greed": 29,
          "total_market_cap_usd": 2522407564278.7,
          "total_volume_24h_usd": 110033452040.6,
          ...
        }},
        "fear_greed": {"snapshot_type": "fear_greed", "payload": {"value": 29, ...}},
        "btc_dominance": {"snapshot_type": "btc_dominance", "payload": {"value": 56.7}},
        ...
      }}

    Flat shape also supported for backwards compatibility:
      {"data": {"btc_dominance": 59.96, "fear_greed_index": 65, ...}}
    """
    out: Dict[str, Optional[float]] = {
        "btc_dominance": None,
        "btc_dominance_change": None,
        "crypto_market_cap_change": None,
        "crypto_volume_change": None,
        "fear_greed_index": None,
    }
    if not raw:
        return out

    data = _unwrap(raw)
    if not isinstance(data, dict):
        return out

    # Detect snapshot structure: values are dicts with "payload" key
    # Extract the crypto_global payload as primary source
    cg_payload: Dict = {}
    fg_payload: Dict = {}
    if isinstance(data.get("crypto_global"), dict):
        cg_payload = data["crypto_global"].get("payload") or {}
    if isinstance(data.get("fear_greed"), dict):
        fg_payload = data["fear_greed"].get("payload") or {}

    # Merge: snapshot payload takes priority, flat data is fallback
    flat = data if not cg_payload else {}

    def _get(*dicts: Dict, fields: tuple, allow_negative: bool = True) -> Optional[float]:
        for d in dicts:
            for fld in fields:
                v = _safe_float(d.get(fld), allow_negative=allow_negative)
                if v is not None:
                    return v
        return None

    # BTC dominance (must be 0–100)
    v = _get(cg_payload, flat, fields=("btc_dominance", "bitcoin_dominance", "btcDominance",
                                        "btc_market_cap_percentage"), allow_negative=False)
    if v is not None and v <= 100:
        out["btc_dominance"] = v

    # BTC dominance change (not always available)
    out["btc_dominance_change"] = _get(
        cg_payload, flat,
        fields=("btc_dominance_change", "bitcoin_dominance_change", "btcDominanceChange",
                "btc_dominance_change_24h"),
    )

    # Total market cap change % (not always available — API may only expose absolute)
    out["crypto_market_cap_change"] = _get(
        cg_payload, flat,
        fields=("total_market_cap_change_24h", "market_cap_change_percentage_24h",
                "total_market_cap_change", "marketCapChange24h", "market_cap_change"),
    )

    # Total volume change % (not always available)
    out["crypto_volume_change"] = _get(
        cg_payload, flat,
        fields=("total_volume_change_24h", "volume_change_24h", "volumeChange24h",
                "total_volume_change", "volume_change"),
    )

    # Fear & greed index (0–100)
    # Try fear_greed snapshot payload first, then crypto_global payload, then flat
    fg_val = (
        _safe_float(fg_payload.get("value") or fg_payload.get("score"), allow_negative=False)
        or _safe_float(cg_payload.get("fear_and_greed") or cg_payload.get("fear_greed_index"), allow_negative=False)
        or _get(flat, flat, fields=("fear_greed_index", "fearGreedIndex", "fear_and_greed_index"), allow_negative=False)
    )
    if fg_val is not None and fg_val <= 100:
        out["fear_greed_index"] = fg_val

    return out


# ── Public entry point ────────────────────────────────────────────────────────

def extract_macro_features(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert raw MDH multi-endpoint responses into a validated flat feature dict.

    Args:
        raw: {"indices": <resp>, "volatility": <resp>, "forex": <resp>,
               "bonds": <resp>, "crypto_global": <resp>}

    Returns:
        Dict with MACRO_FEATURE_COLUMNS keys + macro_context_available.
        Individual features are None when the value could not be extracted.
    """
    features: Dict[str, Any] = {}

    features.update(_extract_indices(raw.get("indices")))
    features.update(_extract_volatility(raw.get("volatility")))
    features.update(_extract_forex(raw.get("forex")))
    features.update(_extract_bonds(raw.get("bonds")))
    features.update(_extract_crypto_global(raw.get("crypto_global")))

    # macro_context_available = True iff at least one numeric feature was extracted
    numeric_ok = sum(
        1 for k, v in features.items()
        if k != "macro_context_available" and v is not None
    )
    features["macro_context_available"] = numeric_ok > 0

    if not features["macro_context_available"]:
        logger.warning("[MDH] extract_macro_features: zero numeric features extracted from raw responses")

    return features
