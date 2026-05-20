"""Symmetric exit-metrics capture (Task #315 / #316).

Single source of truth for the **exit** indicator snapshot persisted in
``trade_tracking.exit_metrics_json`` and ``shadow_trades.features_snapshot_exit``.

Contract (runbook ``exit-metrics-symmetric-capture.md`` §4)
----------------------------------------------------------
1. Catálogo dinâmico — vem do mesmo provider que alimenta os engines de
   decisão (``indicators_provider.build_full_flat_snapshot``). Nunca
   enumerar chaves aqui ou no caller.
2. Formato flat obrigatório (``{key: scalar}``). ``DatasetBuilder``
   quebra com nested (gotcha Task #290).
3. :data:`EXIT_METRICS_INTERNAL_KEYS` é uma constante imutável
   (``Final[frozenset[str]]``) — chaves de controle/observabilidade
   que NÃO entram na comparação de paridade.
4. Contrato de tipos: ``int | float | bool | str | None``. ``dict``/
   ``list`` são dropped + warning estruturado + métrica
   ``scalpyn_exit_metrics_dropped_total{reason="non_scalar"}``.
5. TP/SL/timeout invioláveis — :func:`build_exit_snapshot` NUNCA propaga
   exceção; falha vira ``{"_capture_error": "<repr>"}`` para a UI
   distinguir "fechado sem captura" de "captura quebrou".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Final, Mapping, Optional

from . import indicators_provider

logger = logging.getLogger(__name__)


# ── Chaves internas (constante imutável) ─────────────────────────────────────
#
# Qualquer chave aqui é IGNORADA pela comparação de paridade
# (:func:`validate_parity`) e pela união Entry∪Exit renderizada na UI.
# Adicionar uma chave nova exige justificativa no runbook + atualização
# do test ``test_exit_metrics_helper.py::test_internal_keys_are_immutable``.
EXIT_METRICS_INTERNAL_KEYS: Final[frozenset[str]] = frozenset({
    "_capture_error",
    "system_metadata",
    "timestamps",
})


# ── Prometheus metrics (graceful degradation, mesmo padrão de persistence) ──

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-untyped]
    _PROM_OK = True
except Exception as _exc:  # pragma: no cover — optional dep
    Counter = Gauge = None  # type: ignore[assignment]
    _PROM_OK = False
    logger.debug("prometheus_client unavailable: %s — exit-metrics counters disabled", _exc)


_CAPTURED: Optional["Counter"] = None
_PARITY_MISMATCH: Optional["Counter"] = None
_COVERAGE_PCT: Optional["Gauge"] = None
_DROPPED: Optional["Counter"] = None


def _init_metrics() -> None:
    global _CAPTURED, _PARITY_MISMATCH, _COVERAGE_PCT, _DROPPED
    if not _PROM_OK or _CAPTURED is not None:
        return
    _CAPTURED = Counter(
        "scalpyn_exit_metrics_captured_total",
        "Exit snapshots persisted, partitioned by outcome and status "
        "(ok | capture_error | empty)",
        ["outcome", "status"],
    )
    _PARITY_MISMATCH = Counter(
        "scalpyn_exit_metrics_parity_mismatch_total",
        "Entry↔Exit catalog mismatches detected by validate_parity",
        ["outcome", "reason"],  # reason: missing_in_exit | extra_in_exit | capture_error
    )
    _COVERAGE_PCT = Gauge(
        "scalpyn_exit_metrics_coverage_pct",
        "Latest per-outcome coverage = (entry ∩ exit) / entry * 100",
        ["outcome"],
    )
    _DROPPED = Counter(
        "scalpyn_exit_metrics_dropped_total",
        "Indicator values dropped during exit snapshot construction",
        ["reason"],  # reason: non_scalar
    )


_init_metrics()


def _record_captured(outcome: str, status: str) -> None:
    if _CAPTURED is None:
        return
    try:
        _CAPTURED.labels(outcome=outcome or "unknown", status=status).inc()
    except Exception:
        pass


def _record_mismatch(outcome: str, reason: str) -> None:
    if _PARITY_MISMATCH is None:
        return
    try:
        _PARITY_MISMATCH.labels(outcome=outcome or "unknown", reason=reason).inc()
    except Exception:
        pass


def _record_coverage(outcome: str, pct: float) -> None:
    if _COVERAGE_PCT is None:
        return
    try:
        _COVERAGE_PCT.labels(outcome=outcome or "unknown").set(max(0.0, min(100.0, pct)))
    except Exception:
        pass


def _record_dropped(reason: str, count: int = 1) -> None:
    if _DROPPED is None or count <= 0:
        return
    try:
        _DROPPED.labels(reason=reason).inc(count)
    except Exception:
        pass


# ── Helpers ──────────────────────────────────────────────────────────────────


def _is_scalar(value: Any) -> bool:
    """Contract per runbook §4: int | float | bool | str | None."""
    if value is None:
        return True
    return isinstance(value, (int, float, bool, str))


def flatten_entry_snapshot(entry: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Flatten the nested entry snapshot to ``{key: scalar}`` for parity.

    ``decisions_log.metrics["indicators_snapshot"]`` is persisted in the
    **nested** format ``{key: {"value": ..., "source_group": ..., "ts": ...,
    "stale": ...}}`` (helper :func:`indicators_provider.build_indicators_snapshot`).
    The exit snapshot is **flat** ``{key: scalar}``. To compare catalogs
    one-to-one we collapse the entry to the same shape before passing it
    into :func:`validate_parity`.

    Accepts an already-flat dict transparently (idempotent), so callers
    don't have to inspect the shape themselves.
    """
    if not entry or not isinstance(entry, Mapping):
        return {}
    flat: Dict[str, Any] = {}
    for k, v in entry.items():
        if isinstance(v, Mapping) and "value" in v:
            flat[k] = v.get("value")
        else:
            flat[k] = v
    return flat


# ── Public API ───────────────────────────────────────────────────────────────


async def build_exit_snapshot(db, symbol: str) -> Dict[str, Any]:
    """Build the flat exit snapshot for ``symbol`` and persist-ready.

    Wraps :func:`indicators_provider.build_full_flat_snapshot` to add the
    Task #316 invariants:

    * **NUNCA propaga exceção** — TP/SL/timeout invioláveis. Falha vira
      ``{"_capture_error": "<repr>"}`` + warning estruturado.
    * Drops non-scalar values (defesa contra mudanças no provider) +
      registra ``scalpyn_exit_metrics_dropped_total{reason="non_scalar"}``.
    * Retorna ``{}`` quando o provider devolve catálogo vazio — caller
      decide se grava NULL ou marcador.

    **CRÍTICO** (runbook §3.3): este helper faz I/O de DB. O caller
    DEVE chamá-lo ANTES de abrir a outer transaction (``session.begin``
    /``session.begin_nested``) para não segurar XID e disparar
    ``Lock: transactionid`` (gotcha #251/#273/#310).
    """
    try:
        raw = await indicators_provider.build_full_flat_snapshot(
            db, symbol, include_stale=True
        )
    except Exception as exc:  # noqa: BLE001 — intencional, contrato §4
        logger.warning(
            "[exit_metrics] build_exit_snapshot: provider raised for "
            "symbol=%s — gravando _capture_error (%s)",
            symbol, type(exc).__name__,
        )
        return {"_capture_error": f"{type(exc).__name__}: {exc!s}"}

    if not raw:
        return {}

    cleaned: Dict[str, Any] = {}
    dropped = 0
    for key, value in raw.items():
        if _is_scalar(value):
            cleaned[key] = value
        else:
            dropped += 1
            logger.warning(
                "[exit_metrics] dropped non-scalar value symbol=%s key=%s type=%s",
                symbol, key, type(value).__name__,
            )
    if dropped:
        _record_dropped("non_scalar", dropped)
    return cleaned


def validate_parity(
    entry: Optional[Mapping[str, Any]],
    exit_: Optional[Mapping[str, Any]],
    *,
    trade_id: Any,
    outcome: str,
) -> Dict[str, Any]:
    """Compare entry vs exit catalogs and emit non-blocking telemetry.

    Returns a summary dict ``{status, missing, extra, coverage_pct}`` for
    logging/tests. **Nunca levanta** — diagnóstico puro, sem efeito no
    fechamento.

    ``entry`` pode vir tanto no formato nested (``decisions_log``) quanto
    flat (testes / consumidores futuros) — :func:`flatten_entry_snapshot`
    normaliza idempotentemente.
    """
    entry_flat = flatten_entry_snapshot(entry)
    exit_flat: Dict[str, Any] = dict(exit_ or {})

    # Capture error path — não roda comparação de catálogo, só conta.
    if exit_flat.get("_capture_error") is not None:
        _record_mismatch(outcome, "capture_error")
        _record_captured(outcome, "capture_error")
        logger.warning(
            "[MetricsValidation] trade_id=%s outcome=%s capture_error=%r",
            trade_id, outcome, exit_flat.get("_capture_error"),
        )
        return {
            "status": "capture_error",
            "missing": [],
            "extra": [],
            "coverage_pct": 0.0,
        }

    entry_keys = set(entry_flat.keys()) - EXIT_METRICS_INTERNAL_KEYS
    exit_keys = set(exit_flat.keys()) - EXIT_METRICS_INTERNAL_KEYS

    missing = sorted(entry_keys - exit_keys)
    extra = sorted(exit_keys - entry_keys)

    intersection = entry_keys & exit_keys
    coverage_pct = (
        (len(intersection) / len(entry_keys)) * 100.0
        if entry_keys else 100.0
    )

    if not entry_keys and not exit_keys:
        status = "empty"
    elif not missing and not extra:
        status = "ok"
    else:
        status = "partial_divergence"

    if missing:
        _record_mismatch(outcome, "missing_in_exit")
        logger.info(
            "[MetricsValidation] trade_id=%s outcome=%s missing=%s",
            trade_id, outcome, missing,
        )
    if extra:
        _record_mismatch(outcome, "extra_in_exit")
        logger.info(
            "[MetricsValidation] trade_id=%s outcome=%s extra=%s",
            trade_id, outcome, extra,
        )

    _record_coverage(outcome, coverage_pct)
    _record_captured(outcome, status)

    return {
        "status": status,
        "missing": missing,
        "extra": extra,
        "coverage_pct": round(coverage_pct, 2),
    }
