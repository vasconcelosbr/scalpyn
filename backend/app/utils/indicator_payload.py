"""Compact storage format for governed ``indicators_json`` payloads.

Governed producers (compute_5m, compute_structural_5m, MTF) stamp every
indicator envelope with the same row-level metadata (timeframe, provider,
candle identity, config identity, timestamps ...) plus a per-key
``envelope_hash``.  Repeating ~17 identical fields and an incompressible
64-char hash on 118 keys made each 5m row ~117 KB of JSON (~14 KB after
TOAST compression), growing ``indicators`` by ~1 GB/day.

The compact format stores the fields shared by every envelope once::

    {
      "_envelope": {"format": "compact_v1", "common": {...shared fields...}},
      "adx": {"value": 16.8, "source": "...", "status": "VALID",
              "confidence": 0.8, "period": 14, "parameters": {}},
      ...
    }

``expand_indicator_payload`` rebuilds the legacy shape exactly, including the
same ``envelope_hash`` values (the hash is a pure function of the envelope
content), so readers that verify ``envelope_hash == canonical_hash(material)``
keep working unchanged.  Legacy rows (no ``_envelope`` key) pass through
untouched.

``value``, ``source``, ``status`` and ``confidence`` always stay inside each
key so ``indicators_json->'rsi'->>'value'`` style SQL keeps working.  SQL that
reads other per-key metadata must read the full envelope as::

    (COALESCE(indicators_json->'_envelope'->'common', '{}'::jsonb)
       || (indicators_json->'price'))->>'source_timestamp'

"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Mapping, Optional

from ..services.profile_runtime_config import _canonicalize, canonical_hash

COMPACT_KEY = "_envelope"
COMPACT_FORMAT = "compact_v1"
_PER_KEY_FIELDS = frozenset({"value", "source", "status", "confidence"})


def compact_envelopes_enabled() -> bool:
    """Writers compact only after every reader is deployed (opt-in env flag)."""
    return os.environ.get("INDICATORS_COMPACT_ENVELOPE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def is_compact(payload: Any) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get(COMPACT_KEY), Mapping)


def compact_indicator_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the compact form of a legacy governed payload.

    Only payloads whose every envelope carries a valid ``envelope_hash`` are
    compacted; anything else is returned unchanged so expansion can never add
    or launder a hash the producer did not write.
    """
    if is_compact(payload):
        return dict(payload)
    envelopes = {k: v for k, v in payload.items() if isinstance(v, dict)}
    if not envelopes:
        return dict(payload)
    for envelope in envelopes.values():
        material = dict(envelope)
        expected = material.pop("envelope_hash", None)
        if not expected or expected != canonical_hash(material):
            return dict(payload)
    # Shared value per field = most frequent value among the envelopes, for
    # fields present in every envelope.  Envelopes whose value differs (e.g.
    # live order-flow keys with their own source timestamps) keep an override.
    common: Dict[str, Any] = {}
    first = next(iter(envelopes.values()))
    for field in first:
        if field in _PER_KEY_FIELDS or field == "envelope_hash":
            continue
        if not all(field in env for env in envelopes.values()):
            continue
        counts: Dict[str, list] = {}
        for env in envelopes.values():
            marker = json.dumps(env[field], sort_keys=True, default=str)
            counts.setdefault(marker, [0, env[field]])[0] += 1
        common[field] = max(counts.values(), key=lambda item: item[0])[1]
    out: Dict[str, Any] = {COMPACT_KEY: {"format": COMPACT_FORMAT, "common": common}}
    for key, value in payload.items():
        if isinstance(value, dict):
            out[key] = {
                field: item for field, item in value.items()
                if field != "envelope_hash"
                and not (field in common and _same(item, common[field]))
            }
        else:
            out[key] = value
    return out


def maybe_compact_indicator_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not compact_envelopes_enabled():
        return dict(payload)
    # Verify hashes on the JSON form that will actually be stored, so the
    # expansion on read is checked against exactly what readers will decode.
    return compact_indicator_payload(json.loads(json.dumps(payload, default=str)))


def _same(left: Any, right: Any) -> bool:
    # Type-strict equality: True == 1 == 1.0 must not be collapsed.
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


_ENCODER = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str,
)


def _fast_canonical(value: Any) -> Any:
    """``_canonicalize`` specialised for JSON-decoded values (same output)."""
    kind = type(value)
    if value is None or kind is str or kind is bool or kind is int:
        return value
    if kind is float:
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return int(value) if value.is_integer() else value
    if kind is dict:
        return {str(k): _fast_canonical(v) for k, v in value.items()}
    if kind is list:
        return [_fast_canonical(v) for v in value]
    return _canonicalize(value)


_FRAGMENT_CACHE: Dict[tuple, str] = {}


def _fragment(key: str, value: Any) -> str:
    # Repeated small scalars (source, status, confidence, period ...) are
    # memoised; per-row timestamps and hashes are longer and skip the cache.
    kind = type(value)
    cacheable = value is None or kind in (bool, int) or (kind is str and len(value) <= 24)
    if cacheable:
        cached = _FRAGMENT_CACHE.get((key, kind, value))
        if cached is not None:
            return cached
    fragment = _ENCODER.encode(key) + ":" + _ENCODER.encode(_fast_canonical(value))
    if cacheable:
        if len(_FRAGMENT_CACHE) >= 10_000:
            _FRAGMENT_CACHE.clear()
        _FRAGMENT_CACHE[(key, kind, value)] = fragment
    return fragment


def _hash_from_fragments(fragments: Mapping[str, str]) -> str:
    # Byte-identical to canonical_hash(envelope): canonical JSON is a sorted
    # concatenation of "key":value fragments.
    body = "{" + ",".join(fragments[key] for key in sorted(fragments)) + "}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _expand(envelope: Mapping[str, Any], common: Mapping[str, Any],
            common_fragments: Mapping[str, str]) -> Dict[str, Any]:
    expanded = {**common, **envelope}
    fragments = dict(common_fragments)
    for field, value in envelope.items():
        fragments[str(field)] = _fragment(str(field), value)
    expanded["envelope_hash"] = _hash_from_fragments(fragments)
    return expanded


def _header(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    header = payload.get(COMPACT_KEY)
    if not isinstance(header, Mapping) or header.get("format") != COMPACT_FORMAT:
        return None
    common = header.get("common")
    return dict(common) if isinstance(common, Mapping) else None


def expand_indicator_payload(payload: Any) -> Any:
    """Legacy-shaped payload for any stored row (compact or legacy)."""
    if not is_compact(payload):
        return payload
    common = _header(payload)
    if common is None:
        return {k: v for k, v in payload.items() if k != COMPACT_KEY}
    common_fragments = {str(k): _fragment(str(k), v) for k, v in common.items()}
    return {
        key: _expand(value, common, common_fragments) if isinstance(value, Mapping) else value
        for key, value in payload.items()
        if key != COMPACT_KEY
    }


def expand_indicator_envelope(envelope: Any, header: Any) -> Any:
    """Expand one envelope selected in SQL with ``indicators_json -> '_envelope'``."""
    if not isinstance(envelope, Mapping) or not isinstance(header, Mapping):
        return envelope
    common = _header({COMPACT_KEY: header})
    if common is None:
        return envelope
    common_fragments = {str(k): _fragment(str(k), v) for k, v in common.items()}
    return _expand(envelope, common, common_fragments)

