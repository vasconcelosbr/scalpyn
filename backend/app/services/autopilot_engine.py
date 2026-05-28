"""
autopilot_engine.py
-------------------
Auto-Pilot Engine — autonomous strategy evolution for Strategy Profiles.

Fluxo por ciclo (a cada 6h via Celery beat):
  1. Carrega todos os profiles com auto_pilot_enabled=True
  2. Para cada profile:
     a. Computa métricas de performance (decisions_log + shadow_trades, últimos 30 dias)
     b. Verifica circuit breaker (3 regressões consecutivas → pausa 7 dias)
     c. Detecta regime de mercado atual
     d. Decide se mutação é necessária (triggers configuráveis)
     e. Salva versão atual em profile_versions
     f. Gera nova config via preset_ia_service com contexto de performance
     g. Aplica config e loga em autopilot_audit_logs

Mutation Triggers (qualquer um é suficiente):
  - approved_ev < EV_MIN_THRESHOLD (default -0.30%)
  - fpr > FPR_MAX_THRESHOLD (default 0.65)
  - rejected_ev > approved_ev + SELECTION_INVERSION_DELTA (default 0.50%)
  - horas desde última mutação >= MIN_HOURS_BETWEEN_MUTATIONS (default 48h)
    E EV degradou > 0.20% vs snapshot anterior

Safe Mode (circuit breaker):
  - Se consecutive_regressions >= 3 → pausa por CIRCUIT_BREAKER_PAUSE_HOURS (default 168h = 7 dias)
  - Regressão = EV pós-mutação < EV pré-mutação - 0.10%

Rollback:
  - API POST /autopilot/{profile_id}/rollback/{version_id}
  - Restaura config de profile_versions para profile.config
  - Loga em autopilot_audit_logs com action='ROLLED_BACK'
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("scalpyn.autopilot")

# ── Tuneable constants (override via env if needed) ──────────────────────────
EV_MIN_THRESHOLD = -0.30          # % — abaixo disso, mutar
FPR_MAX_THRESHOLD = 0.65          # ratio — acima disso, mutar
SELECTION_INVERSION_DELTA = 0.50  # % — rejected_ev - approved_ev threshold
MIN_HOURS_BETWEEN_MUTATIONS = 48  # horas mínimas entre mutações
EV_REGRESSION_DELTA = 0.20        # % — degradação para trigger baseado em baseline
MIN_RECORDS_REQUIRED = 30         # amostras mínimas para trigger
CIRCUIT_BREAKER_THRESHOLD = 3     # regressões consecutivas
CIRCUIT_BREAKER_PAUSE_HOURS = 168 # 7 dias
PERFORMANCE_DAYS = 30             # janela de análise (dias)

# ── Outcome vocabulary constants ──────────────────────────────────────────────
# ATENÇÃO: dois vocabulários distintos para o mesmo conceito — NUNCA misturar.
#
#   decisions_log.outcome  → 'tp' / 'sl' / 'timeout'     (lowercase)
#                            gravado por pipeline_scan.py
#
#   shadow_trades.outcome  → 'TP_HIT' / 'SL_HIT' / 'TIMEOUT'  (uppercase)
#                            gravado por shadow_trade_monitor.py
#
# Mapeamento canônico (admin_diagnostics.py:695): TP_HIT→tp, SL_HIT→sl
#
# Se você alterar estes valores, atualize também:
#   backend/app/tasks/pipeline_scan.py      (grava decisions_log.outcome)
#   backend/app/services/shadow_trade_monitor.py  (grava shadow_trades.outcome)
_DL_TP = "tp"
_DL_SL = "sl"
_DL_OUTCOMES_SQL = "('tp', 'sl')"          # pronto para IN (...) em raw SQL

_ST_TP = "TP_HIT"
_ST_SL = "SL_HIT"
_ST_OUTCOMES_SQL = "('TP_HIT', 'SL_HIT')"  # pronto para IN (...) em raw SQL


# ── Guardrails (lidos de config_profiles JSONB — Zero Hardcode) ───────────────
# Defaults seguros usados quando nenhum registro 'autopilot_guardrails' existe.
# dry_run_mode=True por padrão: o sistema nunca escreve sem config explícita no DB.
_GUARDRAILS_DEFAULTS: Dict[str, Any] = {
    "ev_min_threshold_pct":           -0.30,   # fallback = comportamento atual
    "fpr_max_threshold":               0.65,
    "selection_inversion_delta_pct":   0.50,
    "rule_max_delta_per_cycle":        1,
    "rule_points_min":                -10,
    "rule_points_max":                 10,
    "weight_max_delta_per_cycle":      5,
    "threshold_max_delta_per_cycle":   2,
    "min_samples_per_rule":           15,
    "circuit_breaker_threshold":       3,
    "circuit_breaker_pause_hours":   168,
    "kill_switch":                  False,
    "dry_run_mode":                  True,   # SAFE DEFAULT: nunca escreve sem config explícita
    "scope_profile_id":             None,    # None = sem restrição de escopo
}


async def _load_guardrails(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """
    Carrega guardrails do autopilot a partir de config_profiles (config_type='autopilot_guardrails').
    Mescla com _GUARDRAILS_DEFAULTS (DB prevalece sobre defaults).
    Fail-safe: retorna defaults se o registro não existir ou se a query falhar.
    """
    try:
        result = await db.execute(text("""
            SELECT config_json
            FROM config_profiles
            WHERE user_id = CAST(:uid AS uuid)
              AND config_type = 'autopilot_guardrails'
            ORDER BY updated_at DESC
            LIMIT 1
        """), {"uid": str(user_id)})
        row = result.fetchone()
        if row and row[0] and isinstance(row[0], dict):
            merged = dict(_GUARDRAILS_DEFAULTS)
            merged.update(row[0])
            return merged
    except Exception as e:
        logger.warning("[Autopilot] Falha ao carregar guardrails (usando defaults): %s", e)
    return dict(_GUARDRAILS_DEFAULTS)


# ── Performance Analysis ──────────────────────────────────────────────────────

async def compute_performance_window(days: int, db: AsyncSession) -> Dict[str, Any]:
    """
    Computa métricas de performance do pipeline L3 nos últimos N dias.

    Retorna:
        approved_ev          — expected value médio dos trades ALLOWED (%)
        approved_win_rate    — win rate dos trades ALLOWED
        approved_count       — total de amostras ALLOWED com outcome conhecido
        rejected_ev          — EV médio dos shadow trades REJECTED com pnl_pct
        rejected_win_rate    — win rate dos REJECTED
        rejected_count       — total de amostras REJECTED com outcome
        fpr                  — false positive rate (ALLOWED com outcome=sl / total ALLOWED com outcome)
        selection_inversion  — rejected_ev - approved_ev (> 0 = inversão confirmada)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── ALLOWED performance from decisions_log ────────────────────────────────
    # decisions_log vocabulary: _DL_TP='tp', _DL_SL='sl'  (lowercase)
    allowed_result = await db.execute(text(f"""
        SELECT
            COUNT(*)                                             AS n,
            AVG(pnl_pct)                                         AS ev,
            AVG(CASE WHEN outcome = '{_DL_TP}' THEN 1.0 ELSE 0.0 END) AS win_rate,
            SUM(CASE WHEN outcome = '{_DL_SL}' THEN 1 ELSE 0 END)     AS n_sl,
            SUM(CASE WHEN outcome = '{_DL_TP}' THEN 1 ELSE 0 END)     AS n_tp
        FROM decisions_log
        WHERE l3_pass = true
          AND decision = 'ALLOW'
          AND outcome IN {_DL_OUTCOMES_SQL}
          AND pnl_pct IS NOT NULL
          AND created_at >= :cutoff
    """), {"cutoff": cutoff})
    allowed_row = dict(allowed_result.mappings().one())

    n_allowed = int(allowed_row["n"] or 0)
    approved_ev = float(allowed_row["ev"] or 0.0)
    approved_win_rate = float(allowed_row["win_rate"] or 0.0)
    n_sl = int(allowed_row["n_sl"] or 0)
    n_tp = int(allowed_row["n_tp"] or 0)
    fpr = n_sl / n_allowed if n_allowed > 0 else 0.0

    # ── REJECTED performance from shadow_trades ───────────────────────────────
    # shadow_trades vocabulary: _ST_TP='TP_HIT', _ST_SL='SL_HIT'  (UPPERCASE)
    # NÃO confundir com decisions_log — vocabulários diferentes por design.
    rejected_result = await db.execute(text(f"""
        SELECT
            COUNT(*)                                                      AS n,
            AVG(pnl_pct)                                                  AS ev,
            AVG(CASE WHEN outcome = '{_ST_TP}' THEN 1.0 ELSE 0.0 END)   AS win_rate
        FROM shadow_trades
        WHERE source = 'L3_REJECTED'
          AND outcome IN {_ST_OUTCOMES_SQL}
          AND pnl_pct IS NOT NULL
          AND created_at >= :cutoff
    """), {"cutoff": cutoff})
    rejected_row = dict(rejected_result.mappings().one())

    n_rejected = int(rejected_row["n"] or 0)
    rejected_ev = float(rejected_row["ev"] or 0.0)
    rejected_win_rate = float(rejected_row["win_rate"] or 0.0)

    # ── Sanity guard: vocabulary mismatch detector ────────────────────────────
    # Se rejected_count=0 mas existem shadow_trades concluídos, o vocabulário
    # da query provavelmente não bate com o que o shadow_trade_monitor gravou.
    # Ação: inspecione `SELECT DISTINCT outcome FROM shadow_trades WHERE source='L3_REJECTED'`
    if n_rejected == 0:
        _vocab_check = await db.execute(
            text(
                "SELECT COUNT(*) AS n FROM shadow_trades "
                "WHERE source = 'L3_REJECTED' AND pnl_pct IS NOT NULL AND created_at >= :cutoff"
            ),
            {"cutoff": cutoff},
        )
        _n_raw = int(_vocab_check.scalar() or 0)
        if _n_raw > 0:
            logger.warning(
                "[Autopilot] VOCAB_MISMATCH_SUSPECTED: rejected_count=0 "
                "mas %d shadow_trades L3_REJECTED com pnl_pct existem na janela de %d dias. "
                "Vocabulário esperado: outcome IN %s. "
                "Verifique: SELECT DISTINCT outcome FROM shadow_trades WHERE source='L3_REJECTED';",
                _n_raw, days, _ST_OUTCOMES_SQL,
            )

    selection_inversion = rejected_ev - approved_ev

    return {
        "approved_ev":        approved_ev,
        "approved_win_rate":  approved_win_rate,
        "approved_count":     n_allowed,
        "n_tp":               n_tp,
        "n_sl":               n_sl,
        "fpr":                fpr,
        "rejected_ev":        rejected_ev,
        "rejected_win_rate":  rejected_win_rate,
        "rejected_count":     n_rejected,
        "selection_inversion": selection_inversion,
        "analysis_days":      days,
        "computed_at":        datetime.now(timezone.utc).isoformat(),
    }


# ── Regime Detection ──────────────────────────────────────────────────────────

async def detect_regime(db: AsyncSession) -> str:
    """
    Detecta regime de mercado baseado em comportamento recente dos shadow trades.

    Heurística simples mas objetiva:
    - Usa win rate e EV dos últimos 7 dias vs últimos 30 dias
    - HIGH_VOLATILITY: EV > +1.5% ou EV < -2.0% (mercado extremo)
    - BULL: EV > 0 e win_rate > 55%
    - BEAR: EV < -0.5% ou win_rate < 35%
    - SIDEWAYS: demais casos
    """
    try:
        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
        result = await db.execute(text(f"""
            SELECT
                AVG(pnl_pct)                                               AS ev_7d,
                AVG(CASE WHEN outcome = '{_DL_TP}' THEN 1.0 ELSE 0.0 END) AS wr_7d,
                COUNT(*)                                                   AS n_7d
            FROM decisions_log
            WHERE l3_pass = true
              AND decision = 'ALLOW'
              AND outcome IN {_DL_OUTCOMES_SQL}
              AND pnl_pct IS NOT NULL
              AND created_at >= :cutoff
        """), {"cutoff": cutoff_7d})
        row = dict(result.mappings().one())
        ev = float(row["ev_7d"] or 0.0)
        wr = float(row["wr_7d"] or 0.0)
        n = int(row["n_7d"] or 0)

        if n < 5:
            return "SIDEWAYS"  # sem dados suficientes

        if ev > 1.5 or ev < -2.0:
            return "HIGH_VOLATILITY"
        if ev > 0.0 and wr > 0.55:
            return "BULL"
        if ev < -0.5 or wr < 0.35:
            return "BEAR"
        return "SIDEWAYS"
    except Exception as e:
        logger.warning(f"[Autopilot] Falha ao detectar regime: {e}")
        return "SIDEWAYS"


# ── Circuit Breaker ──────────────────────────────────────────────────────────

def _is_circuit_broken(auto_pilot_config: dict) -> bool:
    """
    Retorna True se o circuit breaker está ativo (3+ regressões consecutivas
    dentro da janela de pausa).
    """
    consec = auto_pilot_config.get("consecutive_regressions", 0)
    if consec < CIRCUIT_BREAKER_THRESHOLD:
        return False
    paused_at_str = auto_pilot_config.get("circuit_breaker_paused_at")
    if not paused_at_str:
        return False
    try:
        paused_at = datetime.fromisoformat(paused_at_str)
        pause_until = paused_at + timedelta(hours=CIRCUIT_BREAKER_PAUSE_HOURS)
        return datetime.now(timezone.utc) < pause_until
    except Exception:
        return False


def _check_regression(auto_pilot_config: dict, new_ev: float) -> int:
    """
    Retorna o novo consecutive_regressions após comparar com EV anterior.
    Uma regressão = EV pós-mutação < EV pré-mutação - EV_REGRESSION_DELTA.
    """
    baseline_ev = auto_pilot_config.get("ev_before_last_mutation")
    if baseline_ev is None:
        return 0
    consec = auto_pilot_config.get("consecutive_regressions", 0)
    if new_ev < (float(baseline_ev) - EV_REGRESSION_DELTA):
        return consec + 1
    return 0  # reset on non-regression


# ── Mutation Decision ─────────────────────────────────────────────────────────

def should_mutate(
    perf: Dict[str, Any],
    auto_pilot_config: dict,
    ev_threshold: float = EV_MIN_THRESHOLD,
) -> Tuple[bool, str]:
    """
    Decide se a config do profile deve ser mutada.

    ev_threshold: threshold de EV lido dos guardrails (JSONB). Defaults para
    EV_MIN_THRESHOLD para backward-compat quando chamado sem guardrails.

    Returns: (should_mutate: bool, reason: str)
    """
    n = perf["approved_count"]
    if n < MIN_RECORDS_REQUIRED:
        return False, f"insufficient_data (n={n} < {MIN_RECORDS_REQUIRED})"

    # ── Verificar cooldown ────────────────────────────────────────────────────
    last_mutation_str = auto_pilot_config.get("last_mutation_at")
    if last_mutation_str:
        try:
            last_mutation = datetime.fromisoformat(last_mutation_str)
            hours_elapsed = (datetime.now(timezone.utc) - last_mutation).total_seconds() / 3600
            if hours_elapsed < MIN_HOURS_BETWEEN_MUTATIONS:
                return False, f"cooldown ({hours_elapsed:.1f}h < {MIN_HOURS_BETWEEN_MUTATIONS}h)"
        except Exception:
            pass

    # ── Triggers ─────────────────────────────────────────────────────────────
    ev = perf["approved_ev"]
    fpr = perf["fpr"]
    inversion = perf["selection_inversion"]

    if ev < ev_threshold:
        return True, f"ev_below_threshold (ev={ev:.3f}% < {ev_threshold}%)"

    if fpr > FPR_MAX_THRESHOLD:
        return True, f"fpr_too_high (fpr={fpr:.2f} > {FPR_MAX_THRESHOLD})"

    if inversion > SELECTION_INVERSION_DELTA:
        return True, (
            f"selection_inversion (rejected_ev={perf['rejected_ev']:.3f}% "
            f"vs approved_ev={ev:.3f}%, delta={inversion:.3f}%)"
        )

    # ── EV degradation vs previous baseline ──────────────────────────────────
    baseline_ev = auto_pilot_config.get("ev_after_last_mutation")
    if baseline_ev is not None:
        degradation = float(baseline_ev) - ev
        if degradation > EV_REGRESSION_DELTA:
            return True, f"ev_degraded (baseline={float(baseline_ev):.3f}%, current={ev:.3f}%, delta={degradation:.3f}%)"

    return False, "performance_acceptable"


# ── Version Management ───────────────────────────────────────────────────────

async def save_profile_version(
    profile_id: str,
    config: dict,
    perf: Dict[str, Any],
    regime: str,
    mutation_reason: str,
    db: AsyncSession,
) -> str:
    """Salva snapshot da config atual em profile_versions. Retorna o UUID da versão."""
    # Próximo número de versão
    ver_result = await db.execute(text("""
        SELECT COALESCE(MAX(version_number), 0) + 1
        FROM profile_versions
        WHERE profile_id = :pid
    """), {"pid": profile_id})
    ver_num = int(ver_result.scalar() or 1)

    version_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO profile_versions (
            id, profile_id, version_number, config,
            regime, ev_at_snapshot, win_rate_at_snapshot, fpr_at_snapshot,
            n_samples, mutation_reason, is_active, created_at
        ) VALUES (
            :id, :profile_id, :ver_num, :config,
            :regime, :ev, :wr, :fpr,
            :n, :reason, false, NOW()
        )
    """), {
        "id":        version_id,
        "profile_id": profile_id,
        "ver_num":   ver_num,
        "config":    json.dumps(config),
        "regime":    regime,
        "ev":        perf.get("approved_ev"),
        "wr":        perf.get("approved_win_rate"),
        "fpr":       perf.get("fpr"),
        "n":         perf.get("approved_count"),
        "reason":    mutation_reason,
    })
    return version_id


async def log_audit(
    profile_id: str,
    action: str,
    reason: str,
    regime: str,
    perf: Optional[Dict[str, Any]],
    db: AsyncSession,
    config_before: Optional[dict] = None,
    config_after: Optional[dict] = None,
    version_id: Optional[str] = None,
) -> None:
    """Insere registro em autopilot_audit_logs."""
    await db.execute(text("""
        INSERT INTO autopilot_audit_logs (
            id, profile_id, action, reason, regime,
            perf_snapshot, config_before, config_after, version_id, created_at
        ) VALUES (
            gen_random_uuid(), :pid, :action, :reason, :regime,
            :perf, :before, :after, :ver_id, NOW()
        )
    """), {
        "pid":    profile_id,
        "action": action,
        "reason": reason,
        "regime": regime,
        "perf":   json.dumps(perf) if perf else None,
        "before": json.dumps(config_before) if config_before else None,
        "after":  json.dumps(config_after) if config_after else None,
        "ver_id": version_id,
    })


# ── Config Generation ────────────────────────────────────────────────────────

async def generate_mutated_config(
    profile_id: str,
    profile_role: str,
    current_config: dict,
    perf: Dict[str, Any],
    regime: str,
    user_id: str,
) -> dict:
    """
    Gera nova config via preset_ia_service com contexto enriquecido de performance.
    Injeta dados de performance no current_config como '_autopilot_context'
    para que o Claude tenha acesso durante a geração.
    """
    from .preset_ia_service import run_preset_ia

    # Enriquecer config com contexto de performance (stripped antes de salvar)
    enriched_config = dict(current_config)
    enriched_config["_autopilot_context"] = {
        "performance_window_days": perf.get("analysis_days", PERFORMANCE_DAYS),
        "approved_ev_pct":         round(perf.get("approved_ev", 0), 4),
        "approved_win_rate":       round(perf.get("approved_win_rate", 0), 4),
        "fpr":                     round(perf.get("fpr", 0), 4),
        "rejected_ev_pct":         round(perf.get("rejected_ev", 0), 4),
        "rejected_win_rate":       round(perf.get("rejected_win_rate", 0), 4),
        "selection_inversion_pct": round(perf.get("selection_inversion", 0), 4),
        "regime":                  regime,
        "note": (
            "Auto-Pilot: config gerada com dados reais de performance. "
            f"EV atual={perf.get('approved_ev', 0):.3f}%, "
            f"FPR={perf.get('fpr', 0):.2f}, "
            f"Inversão seleção={perf.get('selection_inversion', 0):.3f}%. "
            "Trades REJEITADOS tiveram melhor performance — relaxar filtros restritivos."
            if perf.get("selection_inversion", 0) > 0.3
            else (
                "Auto-Pilot: config gerada com dados reais de performance. "
                f"EV atual={perf.get('approved_ev', 0):.3f}%, FPR={perf.get('fpr', 0):.2f}."
            )
        ),
    }

    result = await run_preset_ia(
        profile_id=profile_id,
        profile_role=profile_role,
        user_id=user_id,
        current_profile_config=enriched_config,
        db=None,
    )

    # Limpar contexto de autopilot da config gerada (não deve persisitir)
    config = result.get("config", {})
    config.pop("_autopilot_context", None)

    return {
        "config":           config,
        "regime":           result.get("regime", regime),
        "macro_risk":       result.get("macro_risk", "MEDIUM"),
        "analysis_summary": result.get("analysis_summary", ""),
    }


# ── ML-Driven Rule Adjustment ────────────────────────────────────────────────

# Maps scoring rule indicator names → metrics keys in decisions_log.metrics
_INDICATOR_FIELD_MAP: dict[str, str] = {
    "volume_24h":       "volume_24h_usdt",
    "ema_trend":        "ema50_gt_ema200",   # boolean
    "vwap_distance_pct": "vwap_distance_pct",
}

# Min samples required to trust per-rule stats
MIN_RULE_SAMPLES = 15
# Win-rate edge required to trigger a point adjustment
RULE_EDGE_THRESHOLD = 0.10   # 10 percentage points
# Max point delta per autopilot cycle (conservative)
RULE_MAX_DELTA = 1
# Absolute bounds for rule points
RULE_POINTS_MIN = -10
RULE_POINTS_MAX = 10


def _resolve_indicator_key(indicator: str) -> str:
    """Return the metrics dict key for a scoring rule indicator name."""
    return _INDICATOR_FIELD_MAP.get(indicator, indicator)


def _rule_matches(operator: str, val: float, rule: dict) -> bool:
    """Return True if val satisfies the rule condition."""
    try:
        if operator == "between":
            lo = rule.get("min")
            hi = rule.get("max")
            return lo is not None and hi is not None and float(lo) <= val <= float(hi)
        threshold = rule.get("value")
        if threshold is None:
            return False
        threshold = float(threshold)
        if operator in (">", "gt"):
            return val > threshold
        if operator in (">=", "gte"):
            return val >= threshold
        if operator in ("<", "lt"):
            return val < threshold
        if operator in ("<=", "lte"):
            return val <= threshold
    except (TypeError, ValueError):
        pass
    return False


async def compute_rule_insights(
    user_id: str,
    scoring_rules: list,
    db: AsyncSession,
    days: int = PERFORMANCE_DAYS,
) -> dict:
    """
    For each numeric scoring rule compute win-rate and EV from decisions_log.metrics.

    Returns:
        {
          rule_id: { n, win_rate, ev, edge }   ← edge = rule_wr - overall_wr
          "_overall": { n, win_rate }
        }
    Excludes rules with < MIN_RULE_SAMPLES matching trades.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(text(f"""
        SELECT metrics, outcome, pnl_pct
        FROM decisions_log
        WHERE l3_pass = true
          AND decision = 'ALLOW'
          AND outcome IN {_DL_OUTCOMES_SQL}
          AND pnl_pct IS NOT NULL
          AND user_id = :uid
          AND created_at >= :cutoff
    """), {"uid": user_id, "cutoff": cutoff})
    rows = result.mappings().all()

    if not rows:
        return {}

    total = len(rows)
    overall_wins = sum(1 for r in rows if r["outcome"] == _DL_TP)
    overall_wr = overall_wins / total

    insights: dict = {"_overall": {"n": total, "win_rate": overall_wr}}

    for rule in scoring_rules:
        rule_id = rule.get("id")
        indicator = rule.get("indicator", "")
        operator = rule.get("operator", "")

        # Skip boolean-type indicators (ema_trend, ema50>ema200, etc.)
        if ">" in operator and indicator == "ema_trend":
            continue
        if operator in ("is_true", "is_false", "ema50>ema200", "ema9>ema50"):
            continue

        metrics_key = _resolve_indicator_key(indicator)
        matching: list[tuple[bool, float]] = []

        for row in rows:
            metrics = row["metrics"] or {}
            raw = metrics.get(metrics_key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if _rule_matches(operator, val, rule):
                matching.append((row["outcome"] == _DL_TP, float(row["pnl_pct"])))

        n = len(matching)
        if n < MIN_RULE_SAMPLES:
            continue

        wr = sum(1 for w, _ in matching if w) / n
        ev = sum(p for _, p in matching) / n
        insights[rule_id] = {
            "n":        n,
            "win_rate": wr,
            "ev":       ev,
            "edge":     wr - overall_wr,
        }

    return insights


def adjust_rule_points(
    scoring_rules: list,
    insights: dict,
) -> tuple[list, int, list]:
    """
    Apply conservative ±1 point adjustment to rules with strong edge.

    Returns: (adjusted_rules, n_changed, rule_changes)
    rule_changes is a list of dicts with before/after detail for audit logging.
    """
    adjusted = []
    n_changed = 0
    rule_changes: list = []

    for rule in scoring_rules:
        rule = dict(rule)
        info = insights.get(rule.get("id", ""))

        if info and info["n"] >= MIN_RULE_SAMPLES:
            edge = info["edge"]
            current = rule.get("points", 0)

            if edge > RULE_EDGE_THRESHOLD:
                new_pts = min(current + RULE_MAX_DELTA, RULE_POINTS_MAX)
            elif edge < -RULE_EDGE_THRESHOLD:
                new_pts = max(current - RULE_MAX_DELTA, RULE_POINTS_MIN)
            else:
                new_pts = current

            if new_pts != current:
                logger.info(
                    "[Autopilot] Rule %s (%s %s): %+d→%+d  edge=%.1f%%  n=%d",
                    rule.get("id"), rule.get("indicator"), rule.get("operator"),
                    current, new_pts, edge * 100, info["n"],
                )
                rule_changes.append({
                    "rule_id":       rule.get("id"),
                    "indicator":     rule.get("indicator"),
                    "operator":      rule.get("operator"),
                    "min":           rule.get("min"),
                    "max":           rule.get("max"),
                    "value":         rule.get("value"),
                    "points_before": current,
                    "points_after":  new_pts,
                    "edge_pct":      round(edge * 100, 2),
                    "win_rate_pct":  round(info.get("win_rate", 0) * 100, 2),
                    "n_samples":     info["n"],
                })
                rule["points"] = new_pts
                n_changed += 1

        adjusted.append(rule)

    return adjusted, n_changed, rule_changes


async def apply_rule_adjustments(
    profile_id: str,
    user_id: str,
    perf: dict,
    regime: str,
    db: AsyncSession,
    dry_run: bool = False,
    scope_profile_id: Optional[str] = None,
) -> dict:
    """
    Load scoring rules from config_profiles, compute per-rule insights,
    adjust points, persist (or simulate if dry_run=True), and return a summary.

    dry_run=True: executa todo o cálculo mas NÃO persiste alterações.
    scope_profile_id: se definido, bloqueia escrita para qualquer profile_id diferente.
    """
    # Scope check — nunca escreve fora do profile autorizado
    if scope_profile_id and str(profile_id) != str(scope_profile_id):
        msg = f"profile_id={profile_id} fora de scope_profile_id={scope_profile_id}"
        logger.warning("[Autopilot] SCOPE_VIOLATION_BLOCKED (rules): %s", msg)
        await log_audit(profile_id=profile_id, action="SCOPE_VIOLATION_BLOCKED",
                        reason=msg, regime=regime, perf=perf, db=db)
        return {"action": "SCOPE_VIOLATION_BLOCKED", "reason": msg}
    from uuid import UUID
    from sqlalchemy import select
    from ..models.config_profile import ConfigProfile

    try:
        uid = UUID(str(user_id))
        result = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == uid,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "score",
            ).order_by(ConfigProfile.updated_at.desc()).limit(1)
        )
        cp = result.scalars().first()
        if cp is None or not cp.config_json:
            return {"action": "RULES_SKIPPED", "reason": "no_score_config"}

        scoring_rules: list = list(cp.config_json.get("scoring_rules") or cp.config_json.get("rules") or [])
        if not scoring_rules:
            return {"action": "RULES_SKIPPED", "reason": "no_rules_defined"}

        insights = await compute_rule_insights(user_id, scoring_rules, db)
        if not insights or "_overall" not in insights:
            return {"action": "RULES_SKIPPED", "reason": "insufficient_data"}

        adjusted_rules, n_changed, rule_changes = adjust_rule_points(scoring_rules, insights)

        if n_changed == 0:
            return {"action": "RULES_ANALYZED", "reason": "no_adjustment_needed", "insights_n": len(insights) - 1}

        perf_with_changes = {**perf, "rule_changes": rule_changes}

        if dry_run:
            # DRY RUN — calcula mas NÃO persiste. Loga o que SERIA feito.
            await log_audit(
                profile_id=profile_id,
                action="DRY_RUN_RULES_ADJUSTED",
                reason=f"[DRY RUN] {n_changed} scoring rules WOULD be adjusted (not persisted)",
                regime=regime,
                perf=perf_with_changes,
                db=db,
            )
            logger.info(
                "[Autopilot][DRY RUN] %d scoring rules WOULD be adjusted for profile=%s user=%s (not persisted)",
                n_changed, profile_id, user_id,
            )
            return {
                "action": "DRY_RUN_RULES_ADJUSTED",
                "dry_run": True,
                "n_changed": n_changed,
                "insights_n": len(insights) - 1,
                "rule_changes": rule_changes,
            }

        new_config = dict(cp.config_json)
        new_config["scoring_rules"] = adjusted_rules
        cp.config_json = new_config

        await log_audit(
            profile_id=profile_id,
            action="RULES_ADJUSTED",
            reason=f"{n_changed} scoring rules adjusted via ML win-rate analysis",
            regime=regime,
            perf=perf_with_changes,
            db=db,
        )

        logger.info(
            "[Autopilot] %d scoring rules adjusted for profile=%s user=%s",
            n_changed, profile_id, user_id,
        )
        return {"action": "RULES_ADJUSTED", "n_changed": n_changed, "insights_n": len(insights) - 1, "rule_changes": rule_changes}

    except Exception as exc:
        logger.warning("[Autopilot] Rule adjustment failed for profile=%s: %s", profile_id, exc)
        return {"action": "RULES_ERROR", "reason": str(exc)}


# ── Main Cycle ───────────────────────────────────────────────────────────────

async def run_autopilot_cycle(
    profile_id: str,
    profile_role: str,
    user_id: str,
    current_config: dict,
    auto_pilot_config: dict,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Executa um ciclo completo de autopilot para um profile.

    Retorna um dict com action, reason, regime, perf (para logging pelo caller).
    """
    # 0. Carregar guardrails de config_profiles (fail-safe: defaults se ausente)
    guardrails = await _load_guardrails(db, user_id)
    dry_run = bool(guardrails.get("dry_run_mode", True))  # safe default
    ev_threshold = float(guardrails.get("ev_min_threshold_pct", EV_MIN_THRESHOLD))
    scope_id = guardrails.get("scope_profile_id")

    # 0a. Kill-switch — aborta todo o ciclo imediatamente
    if guardrails.get("kill_switch", False):
        logger.warning("[Autopilot] KILL SWITCH ativo — profile=%s abortado.", profile_id)
        await log_audit(
            profile_id=profile_id, action="KILLED",
            reason="kill_switch=true in autopilot_guardrails",
            regime="UNKNOWN", perf=None, db=db,
        )
        await db.commit()
        return {"action": "KILLED", "reason": "kill_switch=true"}

    # 0b. Scope validation — só permite escrita no profile autorizado
    if scope_id and str(profile_id) != str(scope_id):
        msg = f"profile_id={profile_id} fora de scope_profile_id={scope_id}"
        logger.warning("[Autopilot] SCOPE_VIOLATION_BLOCKED: %s", msg)
        await log_audit(
            profile_id=profile_id, action="SCOPE_VIOLATION_BLOCKED",
            reason=msg, regime="UNKNOWN", perf=None, db=db,
        )
        await db.commit()
        return {"action": "SCOPE_VIOLATION_BLOCKED", "reason": msg}

    if dry_run:
        logger.info("[Autopilot][DRY RUN] Ciclo em modo dry-run para profile=%s — nenhuma config será persistida.", profile_id)

    # 1. Verificar circuit breaker
    if _is_circuit_broken(auto_pilot_config):
        paused_at = auto_pilot_config.get("circuit_breaker_paused_at", "")
        reason = f"circuit_breaker_active (paused_at={paused_at})"
        await log_audit(
            profile_id=profile_id,
            action="PAUSED",
            reason=reason,
            regime="UNKNOWN",
            perf=None,
            db=db,
        )
        await db.commit()
        return {"action": "PAUSED", "reason": reason}

    # 2. Computar performance
    try:
        perf = await compute_performance_window(days=PERFORMANCE_DAYS, db=db)
    except Exception as e:
        logger.error(f"[Autopilot] Erro ao computar performance para {profile_id}: {e}")
        return {"action": "ERROR", "reason": str(e)}

    # 3. Detectar regime
    regime = await detect_regime(db)

    # 4. Verificar regressão desde última mutação (atualiza circuit breaker)
    new_consec = _check_regression(auto_pilot_config, perf["approved_ev"])
    updated_ap_config = dict(auto_pilot_config)
    if new_consec != updated_ap_config.get("consecutive_regressions", 0):
        updated_ap_config["consecutive_regressions"] = new_consec
        if new_consec >= CIRCUIT_BREAKER_THRESHOLD:
            updated_ap_config["circuit_breaker_paused_at"] = datetime.now(timezone.utc).isoformat()
            logger.warning(
                f"[Autopilot] CIRCUIT BREAKER ativado para profile {profile_id}: "
                f"{new_consec} regressões consecutivas."
            )

    # 5. Ajustar scoring rules via win-rate por faixa (roda sempre, independente de mutação)
    rule_result = await apply_rule_adjustments(
        profile_id=profile_id,
        user_id=user_id,
        perf=perf,
        regime=regime,
        db=db,
        dry_run=dry_run,
        scope_profile_id=scope_id,
    )

    # 6. Decidir se deve mutar config principal (usa ev_threshold dos guardrails)
    mutate, reason = should_mutate(perf, updated_ap_config, ev_threshold=ev_threshold)
    if not mutate:
        action = "DRY_RUN_ANALYZED" if dry_run else "ANALYZED"
        await log_audit(
            profile_id=profile_id,
            action=action,
            reason=reason,
            regime=regime,
            perf=perf,
            db=db,
        )
        await db.commit()
        return {
            "action": action,
            "dry_run": dry_run,
            "reason": reason,
            "regime": regime,
            "perf": perf,
            "rule_adjustment": rule_result,
        }

    # 7–8. Gerar nova config (necessário mesmo em dry-run — simulamos o resultado)
    try:
        result = await generate_mutated_config(
            profile_id=profile_id,
            profile_role=profile_role,
            current_config=current_config,
            perf=perf,
            regime=regime,
            user_id=user_id,
        )
    except Exception as e:
        logger.error(f"[Autopilot] Erro ao gerar config para {profile_id}: {e}")
        await log_audit(
            profile_id=profile_id,
            action="ERROR",
            reason=f"preset_ia_failed: {e}",
            regime=regime,
            perf=perf,
            db=db,
            config_before=current_config,
        )
        await db.commit()
        return {"action": "ERROR", "reason": str(e)}

    if dry_run:
        # DRY RUN — loga o que SERIA feito sem persistir nada
        logger.info(
            "[Autopilot][DRY RUN] Mutação SIMULADA para profile=%s reason=%s regime=%s (não persistida)",
            profile_id, reason, result["regime"],
        )
        await log_audit(
            profile_id=profile_id,
            action="DRY_RUN_MUTATED",
            reason=f"[DRY RUN] WOULD mutate: {reason}",
            regime=result["regime"],
            perf=perf,
            db=db,
            config_before=current_config,
            config_after=result["config"],
            version_id=None,
        )
        await db.commit()
        return {
            "action":           "DRY_RUN_MUTATED",
            "dry_run":          True,
            "reason":           reason,
            "regime":           result["regime"],
            "perf":             perf,
            "analysis_summary": result.get("analysis_summary"),
            "proposed_config":  result["config"],  # config PROPOSTA, não aplicada
            "rule_adjustment":  rule_result,
        }

    # ── Escrita real (dry_run=False) ─────────────────────────────────────────

    # 7. Salvar versão atual (snapshot pré-mutação)
    try:
        version_id = await save_profile_version(
            profile_id=profile_id,
            config=current_config,
            perf=perf,
            regime=regime,
            mutation_reason=reason,
            db=db,
        )
    except Exception as e:
        logger.error(f"[Autopilot] Erro ao salvar versão para {profile_id}: {e}")
        version_id = None

    # 9. Montar auto_pilot_config atualizado
    updated_ap_config.update({
        "last_mutation_at":        datetime.now(timezone.utc).isoformat(),
        "last_regime":             result["regime"],
        "ev_before_last_mutation": perf["approved_ev"],
        "ev_after_last_mutation":  None,  # será preenchido no próximo ciclo
        "mutation_reason":         reason,
        "last_version_id":         version_id,
        "macro_risk":              result.get("macro_risk"),
        "analysis_summary":        result.get("analysis_summary"),
    })

    # 10. Log de audit
    await log_audit(
        profile_id=profile_id,
        action="MUTATED",
        reason=reason,
        regime=result["regime"],
        perf=perf,
        db=db,
        config_before=current_config,
        config_after=result["config"],
        version_id=version_id,
    )

    await db.commit()

    return {
        "action":           "MUTATED",
        "dry_run":          False,
        "reason":           reason,
        "regime":           result["regime"],
        "perf":             perf,
        "analysis_summary": result.get("analysis_summary"),
        "new_config":       result["config"],
        "updated_ap_config": updated_ap_config,
        "version_id":       version_id,
        "rule_adjustment":  rule_result,
    }


# ── Rollback ─────────────────────────────────────────────────────────────────

async def rollback_to_version(
    profile_id: str,
    version_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Restaura profile.config para um snapshot anterior de profile_versions.
    Loga a ação em autopilot_audit_logs.
    """
    ver_result = await db.execute(text("""
        SELECT id, version_number, config, regime, ev_at_snapshot
        FROM profile_versions
        WHERE id = :vid AND profile_id = :pid
    """), {"vid": version_id, "pid": profile_id})
    ver_row = ver_result.mappings().one_or_none()
    if not ver_row:
        raise ValueError(f"Version {version_id} não encontrada para profile {profile_id}")

    restored_config = ver_row["config"]
    if isinstance(restored_config, str):
        restored_config = json.loads(restored_config)

    await log_audit(
        profile_id=profile_id,
        action="ROLLED_BACK",
        reason=f"Manual rollback to version {ver_row['version_number']} (id={version_id})",
        regime=ver_row.get("regime") or "UNKNOWN",
        perf=None,
        db=db,
        config_after=restored_config,
        version_id=version_id,
    )
    await db.commit()

    return {
        "version_number": ver_row["version_number"],
        "regime":         ver_row.get("regime"),
        "ev_at_snapshot": float(ver_row.get("ev_at_snapshot") or 0),
        "config":         restored_config,
    }
