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
    allowed_result = await db.execute(text("""
        SELECT
            COUNT(*)                                     AS n,
            AVG(pnl_pct)                                 AS ev,
            AVG(CASE WHEN outcome = 'tp' THEN 1.0 ELSE 0.0 END) AS win_rate,
            SUM(CASE WHEN outcome = 'sl' THEN 1 ELSE 0 END)     AS n_sl,
            SUM(CASE WHEN outcome = 'tp' THEN 1 ELSE 0 END)     AS n_tp
        FROM decisions_log
        WHERE l3_pass = true
          AND decision = 'ALLOW'
          AND outcome IN ('tp', 'sl')
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
    rejected_result = await db.execute(text("""
        SELECT
            COUNT(*)                                              AS n,
            AVG(pnl_pct)                                          AS ev,
            AVG(CASE WHEN outcome = 'tp' THEN 1.0 ELSE 0.0 END)  AS win_rate
        FROM shadow_trades
        WHERE source = 'L3_REJECTED'
          AND outcome IN ('tp', 'sl')
          AND pnl_pct IS NOT NULL
          AND created_at >= :cutoff
    """), {"cutoff": cutoff})
    rejected_row = dict(rejected_result.mappings().one())

    n_rejected = int(rejected_row["n"] or 0)
    rejected_ev = float(rejected_row["ev"] or 0.0)
    rejected_win_rate = float(rejected_row["win_rate"] or 0.0)

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
        result = await db.execute(text("""
            SELECT
                AVG(pnl_pct)                                         AS ev_7d,
                AVG(CASE WHEN outcome = 'tp' THEN 1.0 ELSE 0.0 END) AS wr_7d,
                COUNT(*)                                             AS n_7d
            FROM decisions_log
            WHERE l3_pass = true
              AND decision = 'ALLOW'
              AND outcome IN ('tp', 'sl')
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
) -> Tuple[bool, str]:
    """
    Decide se a config do profile deve ser mutada.

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

    if ev < EV_MIN_THRESHOLD:
        return True, f"ev_below_threshold (ev={ev:.3f}% < {EV_MIN_THRESHOLD}%)"

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
        "config":    __import__("json").dumps(config),
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
    import json
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

    # 5. Decidir se deve mutar
    mutate, reason = should_mutate(perf, updated_ap_config)
    if not mutate:
        await log_audit(
            profile_id=profile_id,
            action="ANALYZED",
            reason=reason,
            regime=regime,
            perf=perf,
            db=db,
        )
        await db.commit()
        return {"action": "ANALYZED", "reason": reason, "regime": regime, "perf": perf}

    # 6. Salvar versão atual (snapshot pré-mutação)
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

    # 7. Gerar nova config
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

    # 8. Montar auto_pilot_config atualizado
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

    # 9. Log de audit
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
        "reason":           reason,
        "regime":           result["regime"],
        "perf":             perf,
        "analysis_summary": result.get("analysis_summary"),
        "new_config":       result["config"],
        "updated_ap_config": updated_ap_config,
        "version_id":       version_id,
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
    import json

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
