"""
autopilot_engine.py
-------------------
Auto-Pilot Engine — autonomous strategy evolution for Strategy Profiles.

Fluxo por ciclo (a cada 6h via Celery beat):
  1. Carrega todos os profiles com auto_pilot_enabled=True
  2. Para cada profile:
     a. Computa métricas de performance (shadow_trades source=AUTOPILOT_SOURCE, últimos 30 dias)
        AUTOPILOT_SOURCE=L1_SPECTRUM (default) → desempenho bruto do modelo ML
        AUTOPILOT_SOURCE=L3 (legado)           → comportamento da camada operacional de filtros
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

Behavioral Circuit Breaker (D):
  - Monitoramento de salto súbito na taxa de aprovação (7d vs 30d).
  - Aplicável apenas quando AUTOPILOT_SOURCE=L3 (requer contraparte L3_REJECTED).
  - Quando AUTOPILOT_SOURCE=L1_SPECTRUM: behavioral CB é automaticamente ignorado
    (L1_SPECTRUM não tem stream _REJECTED para calcular approval_rate).
  - Se approval_rate_7d > approval_rate_30d + threshold → BEHAVIORAL_CB_PAUSED.
  - Habilitado por guardrails.behavioral_cb_enabled (default=False — safe).

Performance Auto-Rollback (D):
  - Se consecutive_regressions >= performance_rollback_cycles → restaura último snapshot.
  - Usa profile_versions salvo antes de cada mutação/ajuste.
  - Habilitado por guardrails.performance_rollback_enabled (default=False — safe).
  - Em dry_run: loga DRY_RUN_AUTO_ROLLBACK sem restaurar.

Rollback (manual):
  - API POST /autopilot/{profile_id}/rollback/{version_id}
  - Restaura config de profile_versions para profile.config
  - Loga em autopilot_audit_logs com action='ROLLED_BACK'
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("scalpyn.autopilot")

# ── Source configuration (AUTOPILOT_SOURCE env var) ─────────────────────────
# Controla qual fonte de shadow_trades o Auto-Pilot analisa.
#
#   L1_SPECTRUM  — shadow trades capturados no gate L1 (desempenho bruto do modelo ML).
#                  Todos os trades são "aprovados"; não existe contraparte _REJECTED.
#                  Win rate observada: ~58.6%. Reflete edge do modelo, não da camada L3.
#
#   L3           — comportamento legado: shadow trades promovidos pelo gate L3.
#                  Possui contraparte L3_REJECTED para cálculo de selection_inversion.
#                  Win rate observada: ~51.0%.
#
# Para rollback, basta setar AUTOPILOT_SOURCE=L3 na env do serviço scalpyn (Railway).
AUTOPILOT_SOURCE: str = os.getenv("AUTOPILOT_SOURCE", "L1_SPECTRUM")

# Fonte legacy preservada para rollback e behavioral CB legado
_LEGACY_SOURCE_APPROVED  = "L3"
_LEGACY_SOURCE_REJECTED  = "L3_REJECTED"

# ── Tuneable constants (override via env if needed) ──────────────────────────
EV_MIN_THRESHOLD = -0.30          # % — abaixo disso, mutar
FPR_MAX_THRESHOLD = 0.65          # ratio — acima disso, mutar
SELECTION_INVERSION_DELTA = 0.50  # % — rejected_ev - approved_ev threshold
MIN_HOURS_BETWEEN_MUTATIONS = 48  # horas mínimas entre mutações
EV_REGRESSION_DELTA = 0.20        # % — degradação para trigger baseado em baseline
MIN_RECORDS_REQUIRED = 30         # amostras mínimas para trigger
MIN_SPAN_DAYS = int(os.getenv("AUTOPILOT_MIN_SPAN_DAYS", "5"))  # janela mínima real de dados (dias) antes de mutar
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

# ── TTT outcome vocabulary (shadow_trades.ttt_outcome — migration 065) ────────
# Coluna SEPARADA de shadow_trades.outcome — nunca misturar.
#
#   shadow_trades.ttt_outcome  → 'FAST_WIN' | 'TIMEOUT'
#                                gravado por shadow_trade_monitor._compute_ttt_outcome
#                                ou por ttt_analyzer.py (post-analysis)
#
# LOSS_FUTURE_RESERVED: arquitetura preparada — ainda não usado.
# Quando SL real for implementado: SL_HIT + mfe < ttt_tp_pct → 'LOSS'
_ST_TTT_FAST_WIN = "FAST_WIN"
_ST_TTT_TIMEOUT  = "TIMEOUT"   # mesmo string que outcome TIMEOUT, coluna diferente
_ST_TTT_OUTCOMES_SQL = "('FAST_WIN', 'TIMEOUT')"  # IN (...) para ttt_outcome


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
    "scope_profile_id":             None,    # None = sem escopo; sobrescrito pelo seed do DB
    # C.4 — Autoridade expandida: autopilot pode ajustar todas as dimensões da config.
    # Sem floor/ceiling por decisão do operador — amplitude livre dentro dos guardrails.
    # autopilot_full_authority=True: habilita apply_full_adjustments (block_rules, entry_triggers,
    #   minimum_score além de scoring_rules). "filters" excluído — stub, ver L-07.
    # autopilot_can_adjust: lista de dimensões permitidas (granular). Ignorada se
    #   autopilot_full_authority=False.
    "autopilot_full_authority":     False,   # SAFE DEFAULT: só scoring_rules (comportamento anterior)
    "autopilot_can_adjust": [               # dimensões permitidas quando full_authority=True
        "scoring_rules",
        "minimum_score",
        "block_rules",
        "entry_triggers",
        # "filters" excluído: stub não implementado (L-07) — reintroduzir com clamps em prompt próprio
    ],
    "minimum_score_floor":           0,     # minimum_score não pode descer abaixo deste valor
    "minimum_score_ceiling":        100,    # minimum_score não pode subir acima deste valor
    "min_score_delta_per_cycle":     1,
    # D — Behavioral circuit breaker + performance auto-rollback.
    # SAFE DEFAULT: rollback desabilitado até validação em dry_run.
    #
    # behavioral_cb_enabled: monitora salto súbito na taxa de aprovação.
    #   approval_rate = n_L3 / (n_L3 + n_L3_REJECTED) — últimos 7d vs últimos 30d.
    #   Aplicável apenas quando AUTOPILOT_SOURCE=L3. Ignorado para L1_SPECTRUM.
    #   Se taxa recente > taxa baseline + approval_rate_jump_threshold → pause.
    # performance_rollback_enabled: se consecutive_regressions >= rollback_cycles,
    #   restaura automaticamente o último profile_versions snapshot salvo.
    "fee_limited_guard_enabled":        True,    # bloqueia mutação quando gross_ev > 0 mas net_ev < 0 (fee drag)
    "behavioral_cb_enabled":           False,   # SAFE DEFAULT: desabilitado
    "approval_rate_jump_threshold":    0.30,    # salto de 30 pp na taxa de aprovação
    "approval_rate_min_samples":       20,      # amostras mínimas para calcular taxa
    "performance_rollback_enabled":    False,   # SAFE DEFAULT: desabilitado até validação
    "performance_rollback_cycles":     3,       # ciclos consecutivos ruins antes de rollback
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
        # L-08: warn explicitly when no guardrails record exists so the fallback is visible in logs
        logger.warning(
            "[Autopilot] GUARDRAILS_ABSENT: nenhum registro 'autopilot_guardrails' para user_id=%s "
            "— usando defaults (dry_run=True). Execute backend/sql/seed_autopilot_guardrails.sql.",
            user_id,
        )
    except Exception as e:
        logger.warning("[Autopilot] Falha ao carregar guardrails (usando defaults): %s", e)
    return dict(_GUARDRAILS_DEFAULTS)


async def _load_ml_fee_pct(db: AsyncSession) -> float:
    """Load ml_fee_roundtrip_pct from config_profiles (type='ml').
    Returns 0.0 on any error or missing config — safe fallback for COALESCE queries.
    ZERO HARDCODE: fee is never a literal in SQL or Python.
    """
    try:
        row = await db.execute(text(
            "SELECT config_json->>'ml_fee_roundtrip_pct' AS fee "
            "FROM config_profiles WHERE config_type = 'ml' AND is_active = true LIMIT 1"
        ))
        r = row.one_or_none()
        if r and r.fee is not None:
            return float(r.fee)
    except Exception as _e:
        logger.warning("[Autopilot] ml fee load failed: %s", _e)
    return 0.0


# ── Performance Analysis ──────────────────────────────────────────────────────

async def compute_performance_window(days: int, db: AsyncSession, user_id = None) -> Dict[str, Any]:
    """
    Computa métricas de performance do modelo nos últimos N dias.

    Fonte controlada por AUTOPILOT_SOURCE (env var):
      L1_SPECTRUM (default) — desempenho bruto do modelo ML, capturado no gate L1.
                               Todos os trades são "aprovados"; sem contraparte _REJECTED.
                               rejected_ev / rejected_count / selection_inversion = 0.
      L3 (legado)           — trades aprovados no gate L3 (camada operacional de filtros).
                               Possui contraparte L3_REJECTED para selection_inversion.

    Retorna:
        approved_ev          — expected value médio dos trades com outcome conhecido (%)
        approved_win_rate    — win rate dos trades
        approved_count       — total de amostras com outcome conhecido
        rejected_ev          — EV médio dos REJECTED (0 quando source=L1_SPECTRUM)
        rejected_win_rate    — win rate dos REJECTED (0 quando source=L1_SPECTRUM)
        rejected_count       — total de amostras REJECTED (0 quando source=L1_SPECTRUM)
        fpr                  — false positive rate (outcome=sl / total com outcome)
        selection_inversion  — rejected_ev - approved_ev (0 quando source=L1_SPECTRUM)
        autopilot_source     — fonte usada neste ciclo (para audit trail)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # B3: EV in net-of-fee terms. COALESCE uses net_return_pct when available
    # (post-fix shadows), falls back to pnl_pct - fee for pre-fix records.
    # fee_pct is NEVER a literal — always loaded from config_profiles.
    fee_pct = await _load_ml_fee_pct(db)

    _source = AUTOPILOT_SOURCE  # snapshot local para evitar mudança mid-cycle

    # ── Approved performance from shadow_trades (fonte = AUTOPILOT_SOURCE) ────
    # shadow_trades vocabulary: _ST_TP='TP_HIT', _ST_SL='SL_HIT'  (UPPERCASE)
    # decisions_log permanece como CAPTURA upstream — NÃO removida.
    allowed_result = await db.execute(text(f"""
        SELECT
            COUNT(*)                                                                  AS n,
            AVG(COALESCE(net_return_pct, pnl_pct - :fee_pct))                        AS ev,
            AVG(pnl_pct)                                                              AS gross_ev,
            AVG(CASE WHEN outcome = '{_ST_TP}' THEN 1.0 ELSE 0.0 END)               AS win_rate,
            SUM(CASE WHEN outcome = '{_ST_SL}' THEN 1 ELSE 0 END)                    AS n_sl,
            SUM(CASE WHEN outcome = '{_ST_TP}' THEN 1 ELSE 0 END)                    AS n_tp,
            EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) / 86400.0        AS span_days
        FROM shadow_trades
        WHERE source = :source
          AND outcome IN {_ST_OUTCOMES_SQL}
          AND pnl_pct IS NOT NULL
          AND created_at >= :cutoff
          AND (:uid::text IS NULL OR user_id = :uid)
    """), {"cutoff": cutoff, "fee_pct": fee_pct, "source": _source, "uid": str(user_id) if user_id else None})
    allowed_row = dict(allowed_result.mappings().one())

    n_allowed = int(allowed_row["n"] or 0)
    approved_ev = float(allowed_row["ev"] or 0.0)
    approved_gross_ev = float(allowed_row["gross_ev"] or 0.0)
    approved_win_rate = float(allowed_row["win_rate"] or 0.0)
    n_sl = int(allowed_row["n_sl"] or 0)
    n_tp = int(allowed_row["n_tp"] or 0)
    fpr = n_sl / n_allowed if n_allowed > 0 else 0.0
    span_days = float(allowed_row["span_days"] or 0.0)

    logger.info(
        "[Autopilot] compute_performance_window: source=%s n=%d ev=%.3f%% wr=%.1f%% fpr=%.2f days=%d",
        _source, n_allowed, approved_ev, approved_win_rate * 100, fpr, days,
    )

    # ── Rejected performance (apenas quando source=L3 — L1_SPECTRUM não tem _REJECTED) ─
    # L1_SPECTRUM captura TODOS os trades no gate L1; não existe contraparte rejeitada.
    # selection_inversion permanece disponível no legado L3 para fins de rollback.
    n_rejected = 0
    rejected_ev = 0.0
    rejected_win_rate = 0.0

    if _source == _LEGACY_SOURCE_APPROVED:
        # Comportamento legado L3: calcula rejected e selection_inversion
        # shadow_trades vocabulary: _ST_TP='TP_HIT', _ST_SL='SL_HIT'  (UPPERCASE)
        # NÃO confundir com decisions_log — vocabulários diferentes por design.
        rejected_result = await db.execute(text(f"""
            SELECT
                COUNT(*)                                                                  AS n,
                AVG(COALESCE(net_return_pct, pnl_pct - :fee_pct))                        AS ev,
                AVG(CASE WHEN outcome = '{_ST_TP}' THEN 1.0 ELSE 0.0 END)               AS win_rate
            FROM shadow_trades
            WHERE source = '{_LEGACY_SOURCE_REJECTED}'
              AND outcome IN {_ST_OUTCOMES_SQL}
              AND pnl_pct IS NOT NULL
              AND created_at >= :cutoff
        """), {"cutoff": cutoff, "fee_pct": fee_pct})
        rejected_row = dict(rejected_result.mappings().one())

        n_rejected = int(rejected_row["n"] or 0)
        rejected_ev = float(rejected_row["ev"] or 0.0)
        rejected_win_rate = float(rejected_row["win_rate"] or 0.0)

        # Sanity guard: vocabulary mismatch detector (legado L3)
        # Ação: inspecione `SELECT DISTINCT outcome FROM shadow_trades WHERE source='L3_REJECTED'`
        if n_rejected == 0:
            _vocab_check = await db.execute(
                text(
                    f"SELECT COUNT(*) AS n FROM shadow_trades "
                    f"WHERE source = '{_LEGACY_SOURCE_REJECTED}' AND pnl_pct IS NOT NULL AND created_at >= :cutoff"
                ),
                {"cutoff": cutoff},
            )
            _n_raw = int(_vocab_check.scalar() or 0)
            if _n_raw > 0:
                logger.warning(
                    "[Autopilot] VOCAB_MISMATCH_SUSPECTED: rejected_count=0 "
                    "mas %d shadow_trades %s com pnl_pct existem na janela de %d dias. "
                    "Vocabulário esperado: outcome IN %s. "
                    "Verifique: SELECT DISTINCT outcome FROM shadow_trades WHERE source='%s';",
                    _n_raw, _LEGACY_SOURCE_REJECTED, days, _ST_OUTCOMES_SQL, _LEGACY_SOURCE_REJECTED,
                )

    selection_inversion = rejected_ev - approved_ev

    return {
        "approved_ev":        approved_ev,
        "approved_gross_ev":  approved_gross_ev,
        "approved_win_rate":  approved_win_rate,
        "approved_count":     n_allowed,
        "n_tp":               n_tp,
        "n_sl":               n_sl,
        "fpr":                fpr,
        "span_days":          span_days,
        "rejected_ev":        rejected_ev,
        "rejected_win_rate":  rejected_win_rate,
        "rejected_count":     n_rejected,
        "selection_inversion": selection_inversion,
        "analysis_days":      days,
        "autopilot_source":   _source,
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
        # B3: EV in net-of-fee terms (same COALESCE pattern as compute_performance_window).
        # Usa AUTOPILOT_SOURCE para consistência com compute_performance_window.
        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
        _regime_fee = await _load_ml_fee_pct(db)
        result = await db.execute(text(f"""
            SELECT
                AVG(COALESCE(net_return_pct, pnl_pct - :fee_pct))                AS ev_7d,
                AVG(CASE WHEN outcome = '{_ST_TP}' THEN 1.0 ELSE 0.0 END)       AS wr_7d,
                COUNT(*)                                                           AS n_7d
            FROM shadow_trades
            WHERE source = :source
              AND outcome IN {_ST_OUTCOMES_SQL}
              AND pnl_pct IS NOT NULL
              AND created_at >= :cutoff
        """), {"cutoff": cutoff_7d, "fee_pct": _regime_fee, "source": AUTOPILOT_SOURCE})
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


# ── Behavioral Circuit Breaker (D) ───────────────────────────────────────────


async def check_behavior_circuit_breaker(
    db: AsyncSession,
    perf: Dict[str, Any],
    guardrails: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Detecta salto súbito na taxa de aprovação — sinal de que o autopilot pode ter
    afrouxado filtros/blocos em excesso ou que há bug em alguma mutação.

    Taxa de aprovação = n_L3 / (n_L3 + n_L3_REJECTED) em shadow_trades.

    IMPORTANTE: este check é aplicável apenas quando AUTOPILOT_SOURCE=L3.
    Quando AUTOPILOT_SOURCE=L1_SPECTRUM, não existe stream L3_REJECTED para calcular
    a approval_rate — o check retorna False imediatamente com razão explicativa.

    Lógica (quando source=L3):
      - Calcula taxa nos últimos 7 dias (recente) e últimos 30 dias (baseline).
      - Se taxa_recente > taxa_baseline + approval_rate_jump_threshold → trigger.
      - Requer approval_rate_min_samples em cada janela.

    Returns: (triggered: bool, reason: str)
    """
    if not guardrails.get("behavioral_cb_enabled", False):
        return False, "behavioral_cb_disabled"

    # L1_SPECTRUM não tem contraparte _REJECTED — behavioral CB inaplicável
    if AUTOPILOT_SOURCE != _LEGACY_SOURCE_APPROVED:
        return False, (
            f"behavioral_cb_not_applicable (AUTOPILOT_SOURCE={AUTOPILOT_SOURCE}; "
            f"behavioral CB requer source={_LEGACY_SOURCE_APPROVED}+{_LEGACY_SOURCE_REJECTED})"
        )

    jump_threshold = float(guardrails.get("approval_rate_jump_threshold", 0.30))
    min_samples = int(guardrails.get("approval_rate_min_samples", 20))

    try:
        cutoff_7d  = datetime.now(timezone.utc) - timedelta(days=7)
        cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)

        # Legado L3: queries hardcoded em L3/L3_REJECTED pois este bloco só roda quando
        # AUTOPILOT_SOURCE=L3 (guard acima garante isso).
        result = await db.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE source = '{_LEGACY_SOURCE_APPROVED}'  AND created_at >= :c7d)  AS n_l3_7d,
                COUNT(*) FILTER (WHERE source = '{_LEGACY_SOURCE_REJECTED}'  AND created_at >= :c7d)  AS n_rej_7d,
                COUNT(*) FILTER (WHERE source = '{_LEGACY_SOURCE_APPROVED}'  AND created_at >= :c30d) AS n_l3_30d,
                COUNT(*) FILTER (WHERE source = '{_LEGACY_SOURCE_REJECTED}'  AND created_at >= :c30d) AS n_rej_30d
            FROM shadow_trades
            WHERE source IN ('{_LEGACY_SOURCE_APPROVED}', '{_LEGACY_SOURCE_REJECTED}')
              AND outcome IS NOT NULL
              AND created_at >= :c30d
        """), {"c7d": cutoff_7d, "c30d": cutoff_30d})
        row = dict(result.mappings().one())

        n_l3_7d   = int(row["n_l3_7d"]  or 0)
        n_rej_7d  = int(row["n_rej_7d"] or 0)
        n_l3_30d  = int(row["n_l3_30d"] or 0)
        n_rej_30d = int(row["n_rej_30d"] or 0)

        total_7d  = n_l3_7d + n_rej_7d
        total_30d = n_l3_30d + n_rej_30d

        if total_7d < min_samples or total_30d < min_samples:
            return False, f"behavioral_cb_insufficient_samples (7d={total_7d}, 30d={total_30d})"

        rate_7d  = n_l3_7d  / total_7d
        rate_30d = n_l3_30d / total_30d
        jump = rate_7d - rate_30d

        logger.info(
            "[Autopilot] BehavioralCB: approval_rate 7d=%.1f%% 30d=%.1f%% jump=%.1f%% threshold=%.1f%%",
            rate_7d * 100, rate_30d * 100, jump * 100, jump_threshold * 100,
        )

        if jump > jump_threshold:
            reason = (
                f"BEHAVIORAL_CB_TRIGGERED: approval_rate jumped "
                f"{rate_30d*100:.1f}%→{rate_7d*100:.1f}% "
                f"(+{jump*100:.1f}pp > {jump_threshold*100:.1f}pp threshold) "
                f"n_7d={total_7d} n_30d={total_30d}"
            )
            logger.warning("[Autopilot] %s", reason)
            return True, reason

        return False, f"behavioral_cb_ok (rate_7d={rate_7d*100:.1f}% rate_30d={rate_30d*100:.1f}%)"

    except Exception as exc:
        logger.warning("[Autopilot] BehavioralCB check failed: %s", exc)
        return False, f"behavioral_cb_error:{exc}"


def check_performance_rollback(
    auto_pilot_config: Dict[str, Any],
    guardrails: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Verifica se o número de regressões consecutivas atingiu o limiar de auto-rollback.

    Difere do circuit breaker: este aciona rollback (restaura config), não apenas pausa.
    O circuit breaker pausa mutações futuras; este desfaz a última mutação.

    Returns: (should_rollback: bool, reason: str)
    """
    if not guardrails.get("performance_rollback_enabled", False):
        return False, "performance_rollback_disabled"

    rollback_cycles = int(guardrails.get("performance_rollback_cycles", 3))
    consec = int(auto_pilot_config.get("consecutive_regressions", 0))
    last_version_id = auto_pilot_config.get("last_version_id")

    if consec < rollback_cycles:
        return False, f"performance_ok (consec={consec} < {rollback_cycles})"

    if not last_version_id:
        return False, f"rollback_impossible (no last_version_id, consec={consec})"

    reason = (
        f"PERFORMANCE_ROLLBACK: {consec} consecutive regressions >= {rollback_cycles} "
        f"→ restoring version {last_version_id}"
    )
    logger.warning("[Autopilot] %s", reason)
    return True, reason


async def rollback_last_adjustment(
    profile_id: str,
    user_id: str,
    version_id: str,
    perf: Dict[str, Any],
    regime: str,
    reason: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Restaura atomicamente o snapshot `version_id` de profile_versions para
    todos os config_type que foram afetados pela última mutação.

    Loga em autopilot_audit_logs com action='AUTO_ROLLED_BACK'.
    Reseta consecutive_regressions=0 após o rollback.

    Returns: dict com version_number, config restaurado.
    """
    from uuid import UUID
    from ..models.config_profile import ConfigProfile

    # Busca snapshot
    ver_result = await db.execute(text("""
        SELECT id, version_number, config, regime, ev_at_snapshot
        FROM profile_versions
        WHERE id = :vid AND profile_id = :pid
    """), {"vid": version_id, "pid": profile_id})
    ver_row = ver_result.mappings().one_or_none()

    if not ver_row:
        raise ValueError(
            f"[AutoRollback] version {version_id} não encontrada para profile {profile_id}"
        )

    restored_config = ver_row["config"]
    if isinstance(restored_config, str):
        restored_config = json.loads(restored_config)

    # Determine which config_type this snapshot came from.
    # New snapshots embed [source=score] or [source=block] in mutation_reason.
    # Legacy snapshots (without tag) default to 'score' for backward compatibility.
    _mutation_reason = str(ver_row.get("mutation_reason") or "")
    _restore_config_type = "block" if "[source=block]" in _mutation_reason else "score"

    uid = UUID(str(user_id))
    result = await db.execute(
        select(ConfigProfile).where(
            ConfigProfile.user_id == uid,
            ConfigProfile.pool_id.is_(None),
            ConfigProfile.config_type == _restore_config_type,
        ).order_by(ConfigProfile.updated_at.desc()).limit(1)
    )
    cp = result.scalars().first()
    if cp is not None:
        cp.config_json = restored_config
        from ..services.config_service import config_service as _cs
        await _cs.invalidate_cache(_restore_config_type, uid)

    # Salva nova versão como checkpoint pós-rollback (audit trail)
    checkpoint_id = await save_profile_version(
        profile_id=profile_id,
        config=restored_config,
        perf=perf,
        regime=regime,
        mutation_reason=f"AUTO_ROLLED_BACK from version {ver_row['version_number']}",
        db=db,
    )

    await log_audit(
        profile_id=profile_id,
        action="AUTO_ROLLED_BACK",
        reason=reason,
        regime=regime,
        perf=perf,
        db=db,
        config_after=restored_config,
        version_id=version_id,
    )

    logger.info(
        "[Autopilot] AUTO_ROLLED_BACK profile=%s → version %s (ev_at_snapshot=%.3f%%)",
        profile_id, ver_row["version_number"],
        float(ver_row.get("ev_at_snapshot") or 0),
    )

    return {
        "version_number":   ver_row["version_number"],
        "ev_at_snapshot":   float(ver_row.get("ev_at_snapshot") or 0),
        "regime":           ver_row.get("regime"),
        "config":           restored_config,
        "checkpoint_id":    checkpoint_id,
    }


# ── Mutation Decision ─────────────────────────────────────────────────────────

def should_mutate(
    perf: Dict[str, Any],
    auto_pilot_config: dict,
    ev_threshold: float = EV_MIN_THRESHOLD,
    guardrails: Optional[Dict[str, Any]] = None,
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

    # ── P1-2: Temporal maturity gate ─────────────────────────────────────────
    # Impede mutação quando a janela de dados real é menor que min_span_days.
    # approved_count ≥ MIN_RECORDS_REQUIRED garante volume mas não maturidade temporal.
    # Configurável via guardrails (DB) ou env AUTOPILOT_MIN_SPAN_DAYS.
    min_span = float(guardrails.get("min_span_days", MIN_SPAN_DAYS)) if guardrails else MIN_SPAN_DAYS
    span_days = float(perf.get("span_days", 0.0))
    if span_days < min_span:
        return False, f"imature_window (span_days={span_days:.1f} < {min_span})"

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

    # ── P1-1: FEE_LIMITED guard (configurável via guardrails) ──────────────────
    # Se gross_ev > 0 e net_ev < threshold, o problema é custo (fee drag), não filtros.
    # Configurável: fee_limited_guard_enabled=false desabilita este guard.
    fee_guard = guardrails.get("fee_limited_guard_enabled", True) if guardrails else True
    gross_ev = float(perf.get("approved_gross_ev", ev))
    if fee_guard and gross_ev > 0.0 and ev < ev_threshold:
        return False, (
            f"fee_limited (gross_ev={gross_ev:.3f}% > 0 but net_ev={ev:.3f}% < {ev_threshold}% "
            f"— fee drag, not filter issue)"
        )

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
        "autopilot_source":        perf.get("autopilot_source", AUTOPILOT_SOURCE),
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
    For each numeric scoring rule compute win-rate and EV from shadow_trades.features_snapshot.

    C.1 — migrado de decisions_log.metrics para shadow_trades.features_snapshot.
    features_snapshot = dict flat {indicator: value} — mesmo formato de metrics["indicators_snapshot"].
    decisions_log permanece como CAPTURA upstream — NÃO removida.

    Returns:
        {
          rule_id: { n, win_rate, ev, edge }   ← edge = rule_wr - overall_wr
          "_overall": { n, win_rate }
        }
    Excludes rules with < MIN_RULE_SAMPLES matching trades.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # B3: also fetch net_return_pct; Python-side COALESCE below.
    # Usa AUTOPILOT_SOURCE para consistência com compute_performance_window e detect_regime.
    _insights_fee = await _load_ml_fee_pct(db)
    result = await db.execute(text(f"""
        SELECT features_snapshot, outcome, pnl_pct, net_return_pct
        FROM shadow_trades
        WHERE source = :source
          AND outcome IN {_ST_OUTCOMES_SQL}
          AND pnl_pct IS NOT NULL
          AND features_snapshot IS NOT NULL
          AND user_id = :uid
          AND created_at >= :cutoff
    """), {"uid": user_id, "cutoff": cutoff, "source": AUTOPILOT_SOURCE})
    rows = result.mappings().all()

    if not rows:
        return {}

    total = len(rows)
    overall_wins = sum(1 for r in rows if r["outcome"] == _ST_TP)
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
            # features_snapshot é o dict flat {indicator: value} de shadow_trades
            metrics = row["features_snapshot"] or {}
            raw = metrics.get(metrics_key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if _rule_matches(operator, val, rule):
                # B3: net-of-fee EV; COALESCE: net_return_pct when set, else pnl_pct - fee.
                _pnl = float(row["pnl_pct"])
                _net = row.get("net_return_pct")
                _ev_val = float(_net) if _net is not None else (_pnl - _insights_fee)
                matching.append((row["outcome"] == _ST_TP, _ev_val))

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
    rule_points_min: int = RULE_POINTS_MIN,
    rule_points_max: int = RULE_POINTS_MAX,
    rule_max_delta: int = RULE_MAX_DELTA,
) -> tuple[list, int, list]:
    """
    Apply conservative ±1 point adjustment to rules with strong edge.

    Rules outside [rule_points_min, rule_points_max] are skipped (AUTOPILOT_OUT_OF_RANGE_SKIPPED)
    to prevent the clamp from collapsing pts=40 → 10 in a single cycle.

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

            if rule_points_min <= current <= rule_points_max:
                if edge > RULE_EDGE_THRESHOLD:
                    new_pts = min(current + rule_max_delta, rule_points_max)
                elif edge < -RULE_EDGE_THRESHOLD:
                    new_pts = max(current - rule_max_delta, rule_points_min)
                else:
                    new_pts = current
            else:
                # Out of managed range — skip; manual correction required before autopilot can manage
                logger.warning(
                    "[Autopilot] AUTOPILOT_OUT_OF_RANGE_SKIPPED rule=%s current_pts=%d "
                    "range=[%d,%d] — manual adjustment required",
                    rule.get("id"), current, rule_points_min, rule_points_max,
                )
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
    guardrails: Optional[dict] = None,
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

        _g = guardrails or {}
        _rule_pts_min = int(_g.get("rule_points_min", RULE_POINTS_MIN))
        _rule_pts_max = int(_g.get("rule_points_max", RULE_POINTS_MAX))
        _rule_max_delta = int(_g.get("rule_max_delta_per_cycle", RULE_MAX_DELTA))
        adjusted_rules, n_changed, rule_changes = adjust_rule_points(
            scoring_rules, insights,
            rule_points_min=_rule_pts_min,
            rule_points_max=_rule_pts_max,
            rule_max_delta=_rule_max_delta,
        )

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
        # Invalidate Redis cache after ORM write (L-06)
        from ..services.config_service import config_service as _cs
        await _cs.invalidate_cache("score", uid)

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


# ── Full Authority Adjustments (C.3) ─────────────────────────────────────────
#
# Orquestra ajustes em todas as dimensões da config: scoring_rules, minimum_score,
# block_rules, entry_triggers, filters.
#
# Invariante: salva snapshot em profile_versions ANTES de cada escrita (rollback).
# Sem floor/ceiling por decisão do operador — amplitude livre nos guardrails.
# Controlado por guardrails.autopilot_full_authority (default=False → só scoring_rules).


async def _adjust_minimum_score(
    profile_id: str,
    user_id: str,
    perf: dict,
    regime: str,
    db: AsyncSession,
    dry_run: bool,
    scope_profile_id: Optional[str],
    delta: int = 1,
    guardrails: Optional[dict] = None,
) -> dict:
    """
    Ajusta minimum_score do config_type='score' baseado em FPR e EV.

    Lógica:
      FPR > 0.60  → sobe threshold em +delta (menos aprovações, mais qualidade)
      FPR < 0.30 e approved_ev > 0  → desce threshold em -delta (mais aprovações)
      Sem floor/ceiling: amplitude livre.

    Salva snapshot (rollback) antes de qualquer escrita.
    """
    if scope_profile_id and str(profile_id) != str(scope_profile_id):
        return {"action": "SCOPE_VIOLATION_BLOCKED", "dimension": "minimum_score"}

    from uuid import UUID
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
            return {"action": "SKIPPED", "dimension": "minimum_score", "reason": "no_score_config"}

        current_min = cp.config_json.get("minimum_score")
        if current_min is None:
            return {"action": "SKIPPED", "dimension": "minimum_score", "reason": "field_absent"}

        current_min = int(current_min)
        fpr = perf.get("fpr", 0.0)
        approved_ev = perf.get("approved_ev", 0.0)

        if fpr > 0.60:
            new_min = current_min + delta
            direction = "up"
            reason_msg = f"fpr_high ({fpr:.2f} > 0.60) → raise minimum_score {current_min}→{new_min}"
        elif fpr < 0.30 and approved_ev > 0.0:
            new_min = current_min - delta
            direction = "down"
            reason_msg = f"fpr_low ({fpr:.2f} < 0.30) and ev_positive ({approved_ev:.3f}%) → lower minimum_score {current_min}→{new_min}"
        else:
            return {
                "action": "ANALYZED",
                "dimension": "minimum_score",
                "reason": f"no_adjustment_needed (fpr={fpr:.2f}, ev={approved_ev:.3f}%)",
                "current": current_min,
            }

        # Guardrail clamps: floor and ceiling come from config, never hardcoded (ZERO HARDCODE)
        _g = guardrails or {}
        _floor = int(_g.get("minimum_score_floor", 0))
        _ceiling = int(_g.get("minimum_score_ceiling", 100))
        if not (_floor <= new_min <= _ceiling):
            logger.warning(
                "[Autopilot] AUTOPILOT_MIN_SCORE_OUT_OF_BOUNDS: new_min=%d not in [%d,%d] — skipping",
                new_min, _floor, _ceiling,
            )
            return {
                "action": "SKIPPED",
                "dimension": "minimum_score",
                "reason": f"out_of_bounds(new_min={new_min} not in [{_floor},{_ceiling}])",
            }

        if dry_run:
            logger.info("[Autopilot][DRY RUN] minimum_score WOULD change: %s", reason_msg)
            await log_audit(
                profile_id=profile_id, action="DRY_RUN_MIN_SCORE_ADJUSTED",
                reason=f"[DRY RUN] {reason_msg}", regime=regime, perf=perf, db=db,
            )
            return {"action": "DRY_RUN_MIN_SCORE_ADJUSTED", "dry_run": True,
                    "dimension": "minimum_score", "before": current_min, "after": new_min}

        # Salvar snapshot ANTES da escrita (rollback safety)
        # [source=score] tag identifies which config_type to restore on auto-rollback (L-05)
        snapshot_id = await save_profile_version(
            profile_id=profile_id, config=dict(cp.config_json),
            perf=perf, regime=regime,
            mutation_reason=f"[source=score] pre_min_score_adjustment:{direction}",
            db=db,
        )

        new_config = dict(cp.config_json)
        new_config["minimum_score"] = new_min
        cp.config_json = new_config
        # Invalidate Redis cache after ORM write (L-06)
        from ..services.config_service import config_service as _cs
        await _cs.invalidate_cache("score", uid)

        logger.info("[Autopilot] minimum_score adjusted: %s (snapshot=%s)", reason_msg, snapshot_id)
        await log_audit(
            profile_id=profile_id, action="MIN_SCORE_ADJUSTED",
            reason=reason_msg, regime=regime, perf=perf, db=db,
            config_before={"minimum_score": current_min},
            config_after={"minimum_score": new_min},
            version_id=snapshot_id,
        )
        return {"action": "MIN_SCORE_ADJUSTED", "dimension": "minimum_score",
                "before": current_min, "after": new_min, "snapshot_id": snapshot_id}

    except Exception as exc:
        logger.warning("[Autopilot] minimum_score adjustment failed for profile=%s: %s", profile_id, exc)
        return {"action": "ERROR", "dimension": "minimum_score", "reason": str(exc)}


async def _adjust_block_rules(
    profile_id: str,
    user_id: str,
    insights: dict,
    perf: dict,
    regime: str,
    db: AsyncSession,
    dry_run: bool,
    scope_profile_id: Optional[str],
) -> dict:
    """
    Habilita/desabilita block_rules com base em edge de win-rate por indicador.

    Lógica:
      Se um block_rule bloqueia trades em um range e o edge desse range é POSITIVO
      (o range produz wins), desabilita o bloco (estávamos bloqueando ganhos).
      Se o edge é NEGATIVO (range produz losses), habilita o bloco (bloquear é correto).

    Config lida de config_type='block' → config_json["block_rules"]["blocks"].
    Salva snapshot ANTES de qualquer escrita.
    """
    if scope_profile_id and str(profile_id) != str(scope_profile_id):
        return {"action": "SCOPE_VIOLATION_BLOCKED", "dimension": "block_rules"}

    overall_wr = insights.get("_overall", {}).get("win_rate", 0.5)
    if not insights or "_overall" not in insights:
        return {"action": "SKIPPED", "dimension": "block_rules", "reason": "no_insights"}

    from uuid import UUID
    from ..models.config_profile import ConfigProfile

    try:
        uid = UUID(str(user_id))
        result = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == uid,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "block",
            ).order_by(ConfigProfile.updated_at.desc()).limit(1)
        )
        cp = result.scalars().first()
        if cp is None or not cp.config_json:
            return {"action": "SKIPPED", "dimension": "block_rules", "reason": "no_block_config"}

        blocks = list((cp.config_json.get("block_rules") or {}).get("blocks") or [])
        if not blocks:
            return {"action": "SKIPPED", "dimension": "block_rules", "reason": "no_blocks_defined"}

        changes = []
        adjusted = []
        for block in blocks:
            block = dict(block)
            indicator = block.get("field") or block.get("indicator", "")
            rule_key = _resolve_indicator_key(indicator)
            info = insights.get(rule_key) or insights.get(indicator)
            currently_enabled = bool(block.get("enabled", True))

            if info and info["n"] >= MIN_RULE_SAMPLES:
                edge = info["edge"]   # positive edge = this range wins → should NOT be blocked
                should_enable = edge < -RULE_EDGE_THRESHOLD  # negative edge → blocking is correct
                if should_enable != currently_enabled:
                    changes.append({
                        "block_id": block.get("id"),
                        "field": indicator,
                        "edge_pct": round(edge * 100, 2),
                        "n_samples": info["n"],
                        "before_enabled": currently_enabled,
                        "after_enabled": should_enable,
                    })
                    block["enabled"] = should_enable
                    logger.info(
                        "[Autopilot] block_rule %s (%s): enabled %s→%s edge=%.1f%%",
                        block.get("id"), indicator, currently_enabled, should_enable, edge * 100,
                    )
            adjusted.append(block)

        if not changes:
            return {"action": "ANALYZED", "dimension": "block_rules", "reason": "no_adjustment_needed"}

        if dry_run:
            logger.info("[Autopilot][DRY RUN] block_rules WOULD change: %d toggles", len(changes))
            await log_audit(
                profile_id=profile_id, action="DRY_RUN_BLOCK_RULES_ADJUSTED",
                reason=f"[DRY RUN] {len(changes)} block_rules WOULD be toggled",
                regime=regime, perf={**perf, "block_changes": changes}, db=db,
            )
            return {"action": "DRY_RUN_BLOCK_RULES_ADJUSTED", "dry_run": True,
                    "dimension": "block_rules", "n_changed": len(changes), "changes": changes}

        # Snapshot ANTES da escrita
        snapshot_id = await save_profile_version(
            profile_id=profile_id, config=dict(cp.config_json),
            perf=perf, regime=regime,
            mutation_reason=f"[source=block] pre_block_rules_adjustment:{len(changes)}_toggles",
            db=db,
        )

        new_config = dict(cp.config_json)
        new_config["block_rules"] = dict(new_config.get("block_rules") or {})
        new_config["block_rules"]["blocks"] = adjusted
        cp.config_json = new_config
        # Invalidate Redis cache after ORM write (L-06)
        from uuid import UUID as _UUID
        _uid = _UUID(str(user_id))
        from ..services.config_service import config_service as _cs
        await _cs.invalidate_cache("block", _uid)

        await log_audit(
            profile_id=profile_id, action="BLOCK_RULES_ADJUSTED",
            reason=f"{len(changes)} block_rules toggled via edge analysis",
            regime=regime, perf={**perf, "block_changes": changes}, db=db,
            config_after={"block_rules": {"blocks": adjusted}},
            version_id=snapshot_id,
        )
        return {"action": "BLOCK_RULES_ADJUSTED", "dimension": "block_rules",
                "n_changed": len(changes), "changes": changes, "snapshot_id": snapshot_id}

    except Exception as exc:
        logger.warning("[Autopilot] block_rules adjustment failed for profile=%s: %s", profile_id, exc)
        return {"action": "ERROR", "dimension": "block_rules", "reason": str(exc)}


async def _adjust_entry_triggers(
    profile_id: str,
    user_id: str,
    insights: dict,
    perf: dict,
    regime: str,
    db: AsyncSession,
    dry_run: bool,
    scope_profile_id: Optional[str],
) -> dict:
    """
    Habilita/desabilita entry_triggers com base em edge de win-rate por indicador.

    Condição com edge POSITIVO (range vence) → habilitar (queremos entrar quando esse range).
    Condição com edge NEGATIVO (range perde) → desabilitar.

    Config lida de config_type='block' → config_json["entry_triggers"]["conditions"].
    Salva snapshot ANTES de qualquer escrita.
    """
    if scope_profile_id and str(profile_id) != str(scope_profile_id):
        return {"action": "SCOPE_VIOLATION_BLOCKED", "dimension": "entry_triggers"}

    if not insights or "_overall" not in insights:
        return {"action": "SKIPPED", "dimension": "entry_triggers", "reason": "no_insights"}

    from uuid import UUID
    from ..models.config_profile import ConfigProfile

    try:
        uid = UUID(str(user_id))
        result = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == uid,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "block",
            ).order_by(ConfigProfile.updated_at.desc()).limit(1)
        )
        cp = result.scalars().first()
        if cp is None or not cp.config_json:
            return {"action": "SKIPPED", "dimension": "entry_triggers", "reason": "no_block_config"}

        conditions = list((cp.config_json.get("entry_triggers") or {}).get("conditions") or [])
        if not conditions:
            return {"action": "SKIPPED", "dimension": "entry_triggers", "reason": "no_conditions_defined"}

        changes = []
        adjusted = []
        for cond in conditions:
            cond = dict(cond)
            indicator = cond.get("indicator", "")
            rule_key = _resolve_indicator_key(indicator)
            info = insights.get(rule_key) or insights.get(indicator)
            currently_enabled = bool(cond.get("enabled", True))

            if info and info["n"] >= MIN_RULE_SAMPLES:
                edge = info["edge"]  # positive = this range wins → should be required for entry
                should_enable = edge > RULE_EDGE_THRESHOLD
                if should_enable != currently_enabled:
                    changes.append({
                        "condition_id": cond.get("id"),
                        "indicator": indicator,
                        "edge_pct": round(edge * 100, 2),
                        "n_samples": info["n"],
                        "before_enabled": currently_enabled,
                        "after_enabled": should_enable,
                    })
                    cond["enabled"] = should_enable
                    logger.info(
                        "[Autopilot] entry_trigger %s (%s): enabled %s→%s edge=%.1f%%",
                        cond.get("id"), indicator, currently_enabled, should_enable, edge * 100,
                    )
            adjusted.append(cond)

        if not changes:
            return {"action": "ANALYZED", "dimension": "entry_triggers", "reason": "no_adjustment_needed"}

        if dry_run:
            logger.info("[Autopilot][DRY RUN] entry_triggers WOULD change: %d toggles", len(changes))
            await log_audit(
                profile_id=profile_id, action="DRY_RUN_ENTRY_TRIGGERS_ADJUSTED",
                reason=f"[DRY RUN] {len(changes)} entry_triggers WOULD be toggled",
                regime=regime, perf={**perf, "trigger_changes": changes}, db=db,
            )
            return {"action": "DRY_RUN_ENTRY_TRIGGERS_ADJUSTED", "dry_run": True,
                    "dimension": "entry_triggers", "n_changed": len(changes), "changes": changes}

        # Snapshot ANTES da escrita
        snapshot_id = await save_profile_version(
            profile_id=profile_id, config=dict(cp.config_json),
            perf=perf, regime=regime,
            mutation_reason=f"[source=block] pre_entry_triggers_adjustment:{len(changes)}_toggles",
            db=db,
        )

        new_config = dict(cp.config_json)
        new_config["entry_triggers"] = dict(new_config.get("entry_triggers") or {})
        new_config["entry_triggers"]["conditions"] = adjusted
        cp.config_json = new_config
        # Invalidate Redis cache after ORM write (L-06)
        from uuid import UUID as _UUID
        _uid = _UUID(str(user_id))
        from ..services.config_service import config_service as _cs
        await _cs.invalidate_cache("block", _uid)

        await log_audit(
            profile_id=profile_id, action="ENTRY_TRIGGERS_ADJUSTED",
            reason=f"{len(changes)} entry_triggers toggled via edge analysis",
            regime=regime, perf={**perf, "trigger_changes": changes}, db=db,
            config_after={"entry_triggers": {"conditions": adjusted}},
            version_id=snapshot_id,
        )
        return {"action": "ENTRY_TRIGGERS_ADJUSTED", "dimension": "entry_triggers",
                "n_changed": len(changes), "changes": changes, "snapshot_id": snapshot_id}

    except Exception as exc:
        logger.warning("[Autopilot] entry_triggers adjustment failed for profile=%s: %s", profile_id, exc)
        return {"action": "ERROR", "dimension": "entry_triggers", "reason": str(exc)}


async def apply_full_adjustments(
    profile_id: str,
    user_id: str,
    perf: dict,
    regime: str,
    insights: dict,
    db: AsyncSession,
    dry_run: bool = False,
    scope_profile_id: Optional[str] = None,
    can_adjust: Optional[list] = None,
    min_score_delta: int = 1,
    guardrails: Optional[dict] = None,
) -> dict:
    """
    Orquestra todos os ajustes de config com autoridade expandida (C.3).

    Dimensões controladas por `can_adjust` (lida dos guardrails):
      - "scoring_rules"   → apply_rule_adjustments (comportamento anterior)
      - "minimum_score"   → _adjust_minimum_score
      - "block_rules"     → _adjust_block_rules
      - "entry_triggers"  → _adjust_entry_triggers
      - "filters"         → reservado (stub)

    Invariante: cada dimensão que ESCREVE salva snapshot antes (rollback safety).
    Sem floor/ceiling por decisão do operador.

    Returns: dict com resultado por dimensão.
    """
    if can_adjust is None:
        can_adjust = ["scoring_rules"]

    results: dict = {}

    # ── scoring_rules ─────────────────────────────────────────────────────────
    if "scoring_rules" in can_adjust:
        scoring_rules_list = insights.get("_scoring_rules_list", [])
        results["scoring_rules"] = await apply_rule_adjustments(
            profile_id=profile_id,
            user_id=user_id,
            perf=perf,
            regime=regime,
            db=db,
            dry_run=dry_run,
            scope_profile_id=scope_profile_id,
            guardrails=guardrails,
        )

    # ── minimum_score ─────────────────────────────────────────────────────────
    if "minimum_score" in can_adjust:
        results["minimum_score"] = await _adjust_minimum_score(
            profile_id=profile_id,
            user_id=user_id,
            perf=perf,
            regime=regime,
            db=db,
            dry_run=dry_run,
            scope_profile_id=scope_profile_id,
            delta=min_score_delta,
            guardrails=guardrails,
        )

    # ── block_rules ───────────────────────────────────────────────────────────
    if "block_rules" in can_adjust:
        results["block_rules"] = await _adjust_block_rules(
            profile_id=profile_id,
            user_id=user_id,
            insights=insights,
            perf=perf,
            regime=regime,
            db=db,
            dry_run=dry_run,
            scope_profile_id=scope_profile_id,
        )

    # ── entry_triggers ────────────────────────────────────────────────────────
    if "entry_triggers" in can_adjust:
        results["entry_triggers"] = await _adjust_entry_triggers(
            profile_id=profile_id,
            user_id=user_id,
            insights=insights,
            perf=perf,
            regime=regime,
            db=db,
            dry_run=dry_run,
            scope_profile_id=scope_profile_id,
        )

    # ── filters (reservado — stub) ─────────────────────────────────────────
    if "filters" in can_adjust:
        results["filters"] = {"action": "SKIPPED", "dimension": "filters", "reason": "not_implemented"}

    return results


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

    # 1. Verificar circuit breaker de performance (regressões acumuladas — pausa)
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

    # Audit P0-11: populate ev_after_last_mutation from the previous cycle.
    # On the cycle *after* a mutation, ev_after_last_mutation is None; fill it
    # with the first observed EV so _check_regression can compare next cycle.
    if auto_pilot_config.get("ev_after_last_mutation") is None and auto_pilot_config.get("last_mutation_at"):
        auto_pilot_config["ev_after_last_mutation"] = perf["approved_ev"]
        logger.info(
            "[Autopilot] P0-11: populated ev_after_last_mutation=%.4f for profile=%s",
            perf["approved_ev"], profile_id,
        )

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

    # 4a. D — Behavioral circuit breaker (salto súbito na taxa de aprovação)
    # Roda APÓS ter performance calculada. Independente de dry_run — só pausa, não escreve.
    beh_triggered, beh_reason = await check_behavior_circuit_breaker(
        db=db, perf=perf, guardrails=guardrails,
    )
    if beh_triggered:
        await log_audit(
            profile_id=profile_id,
            action="BEHAVIORAL_CB_PAUSED",
            reason=beh_reason,
            regime=regime,
            perf=perf,
            db=db,
        )
        await db.commit()
        return {"action": "BEHAVIORAL_CB_PAUSED", "reason": beh_reason, "perf": perf}

    # 4b. D — Performance auto-rollback (N ciclos ruins consecutivos → restaura última versão)
    # Roda antes dos ajustes para evitar que ciclo atual piore mais antes de rollback.
    rollback_needed, rollback_reason = check_performance_rollback(
        auto_pilot_config=updated_ap_config, guardrails=guardrails,
    )
    if rollback_needed:
        last_version_id = updated_ap_config.get("last_version_id")
        if dry_run:
            logger.info(
                "[Autopilot][DRY RUN] AUTO_ROLLBACK WOULD restore version=%s for profile=%s (not persisted)",
                last_version_id, profile_id,
            )
            await log_audit(
                profile_id=profile_id,
                action="DRY_RUN_AUTO_ROLLBACK",
                reason=f"[DRY RUN] WOULD rollback: {rollback_reason}",
                regime=regime, perf=perf, db=db,
            )
            await db.commit()
            return {
                "action": "DRY_RUN_AUTO_ROLLBACK",
                "dry_run": True,
                "reason": rollback_reason,
                "regime": regime,
                "perf": perf,
                "would_restore_version": last_version_id,
            }
        try:
            rollback_result = await rollback_last_adjustment(
                profile_id=profile_id,
                user_id=user_id,
                version_id=last_version_id,
                perf=perf,
                regime=regime,
                reason=rollback_reason,
                db=db,
            )
            # Reseta contador de regressões após rollback bem-sucedido
            updated_ap_config["consecutive_regressions"] = 0
            updated_ap_config["circuit_breaker_paused_at"] = None
            await db.commit()
            return {
                "action":          "AUTO_ROLLED_BACK",
                "reason":          rollback_reason,
                "regime":          regime,
                "perf":            perf,
                "version_number":  rollback_result["version_number"],
                "ev_at_snapshot":  rollback_result["ev_at_snapshot"],
            }
        except Exception as exc:
            logger.error(
                "[Autopilot] AUTO_ROLLBACK falhou para profile=%s version=%s: %s",
                profile_id, last_version_id, exc,
            )
            # Não aborta o ciclo — loga e continua para evitar que rollback quebrado
            # impeça análise futura.
            await log_audit(
                profile_id=profile_id,
                action="AUTO_ROLLBACK_FAILED",
                reason=f"rollback_error: {exc} (version={last_version_id})",
                regime=regime, perf=perf, db=db,
            )

    # 5. Ajustar config via full_authority (C.3) — roda sempre, independente de mutação.
    # Se autopilot_full_authority=False (default), comporta-se como antes: só scoring_rules.
    full_authority = bool(guardrails.get("autopilot_full_authority", False))
    can_adjust = list(guardrails.get("autopilot_can_adjust", ["scoring_rules"]))
    if not full_authority:
        can_adjust = ["scoring_rules"]   # fallback seguro: só scoring rules
    min_score_delta = int(guardrails.get("min_score_delta_per_cycle", 1))

    # Carrega insights uma vez para todas as dimensões que precisam deles
    from uuid import UUID as _UUID
    from ..models.config_profile import ConfigProfile as _CP
    _score_result = await db.execute(
        select(_CP).where(
            _CP.user_id == _UUID(str(user_id)),
            _CP.pool_id.is_(None),
            _CP.config_type == "score",
        ).order_by(_CP.updated_at.desc()).limit(1)
    )
    _score_cp = _score_result.scalars().first()
    _scoring_rules_list = list(
        (_score_cp.config_json.get("scoring_rules") or _score_cp.config_json.get("rules") or [])
        if (_score_cp and _score_cp.config_json) else []
    )
    insights = await compute_rule_insights(user_id, _scoring_rules_list, db)
    insights["_scoring_rules_list"] = _scoring_rules_list  # passa para apply_full_adjustments

    rule_result = await apply_full_adjustments(
        profile_id=profile_id,
        user_id=user_id,
        perf=perf,
        regime=regime,
        insights=insights,
        db=db,
        dry_run=dry_run,
        scope_profile_id=scope_id,
        can_adjust=can_adjust,
        min_score_delta=min_score_delta,
        guardrails=guardrails,
    )

    # 6. Decidir se deve mutar config principal (usa ev_threshold dos guardrails)
    mutate, reason = should_mutate(perf, updated_ap_config, ev_threshold=ev_threshold, guardrails=guardrails)
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
            "updated_ap_config": updated_ap_config,  # Audit P0-10: allow caller to persist CB state
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
