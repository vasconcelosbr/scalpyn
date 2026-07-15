"""Fase 1 — Blocos C/D: certificação de integridade do dataset ML.

Executa a query de certificação (invariantes I01–I11, espelhada em
backend/sql/fase1_certification_integrity.sql), a query cumulativa e os
WARNs de observação, persiste uma linha por execução em
ml_data_certification_runs e emite alerta pelo canal LOG_ONLY (D2).

Regras vinculantes:
- População canônica: source='L1_SPECTRUM' AND barrier_mode='ATR_DYNAMIC'.
- COL_PNL = pnl_pct (DECISÃO A-1: net_pnl_pct não existe em shadow_trades).
- I09 é FAIL na execução do job; informativo em janelas históricas.
- Status agregado: GREEN = todos PASS; RED = qualquer FAIL; YELLOW = só WARNs.
- Idempotência: re-execução na mesma janela persiste a linha mas não duplica
  o alerta (dedupe pelo conjunto de invariantes violados da execução anterior).
- Read-only sobre shadow_trades/decisions_log; a única escrita é o INSERT
  em ml_data_certification_runs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
except ModuleNotFoundError:  # pragma: no cover
    from backend.app.ml.dataset_config import parse_required_ml_dataset_valid_from

logger = logging.getLogger(__name__)

CANONICAL_SOURCE = "L1_SPECTRUM"
CANONICAL_BARRIER_MODE = "ATR_DYNAMIC"

# Janela do job: 26h sobreposta de propósito — nenhuma linha escapa entre
# execuções de 2h.
JOB_WINDOW_HOURS = 26

_INVARIANTS_SQL = text("""
WITH pop AS (
  SELECT * FROM shadow_trades
  WHERE source = :src
    AND barrier_mode = :bmode
    AND entry_timestamp >= :w_from
    AND entry_timestamp <  :w_to
)
SELECT 'I01_outcome_casing' AS invariante,
       COUNT(*) AS violacoes,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM pop WHERE outcome IS NOT NULL AND outcome <> UPPER(outcome)
UNION ALL
SELECT 'I02_contratos_nulos_em_elegiveis', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE eligible_for_training IS TRUE
  AND (feature_schema_version IS NULL OR label_contract_version IS NULL
       OR barrier_contract_version IS NULL OR capture_contract_version IS NULL)
UNION ALL
SELECT 'I03_elegivel_pre_valid_from', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM shadow_trades
WHERE eligible_for_training IS TRUE AND entry_timestamp < :valid_from
UNION ALL
SELECT 'I04_snapshot_incompleto', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE config_snapshot->>'barrier_mode' IS NULL
   OR config_snapshot->>'atr_multiplier_sl' IS NULL
   OR config_snapshot->>'win_fast_threshold_seconds' IS NULL
UNION ALL
SELECT 'I05_flag_x_lineage_divergente', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE (eligible_for_training IS TRUE AND lineage_status IS DISTINCT FROM 'EXACT')
   OR (eligible_for_training IS FALSE AND lineage_status = 'EXACT')
UNION ALL
SELECT 'I06_coverage_baixa_em_elegiveis', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE eligible_for_training IS TRUE
  AND (features_coverage IS NULL OR features_coverage < 0.8)
UNION ALL
SELECT 'I07_tp_hit_pnl_negativo', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE outcome = 'TP_HIT' AND pnl_pct < 0
UNION ALL
SELECT 'I08_atr_nulo_em_completed_acima_de_meio_pct',
       COUNT(*) FILTER (WHERE atr_pct_at_entry IS NULL),
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            WHEN COUNT(*) FILTER (WHERE atr_pct_at_entry IS NULL)::numeric / COUNT(*) <= 0.005
            THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE status = 'COMPLETED'
UNION ALL
SELECT 'I09_geracao_abaixo_do_piso',
       COUNT(*),
       CASE WHEN COUNT(*) >= :piso_d3 THEN 'PASS' ELSE 'FAIL' END
FROM pop
UNION ALL
SELECT 'I10_duplicidade_elegivel', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM (SELECT symbol, entry_timestamp FROM pop
      WHERE eligible_for_training IS TRUE
      GROUP BY 1, 2 HAVING COUNT(*) > 1) d
UNION ALL
SELECT 'I11_holding_negativo', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE holding_seconds < 0
UNION ALL
-- Fase 1.4 (P1, cenário VERDE, ação A): cobertura da lane L3/L3_LAB.
-- Invariante DEDICADO (não estende o I04) para preservar a série histórica
-- do I04, que permanece L1_SPECTRUM+ATR_DYNAMIC sobre config_snapshot.
-- I12 checa as COLUNAS DEDICADAS que o treino realmente lê
-- (_filter_l3_barrier_contract/_economic_contract_features), não o JSONB
-- config_snapshot (que o builder ignora — ml_challenger_service.py:850).
SELECT 'I12_l3_economic_contract',
       COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM shadow_trades
WHERE source IN ('L3', 'L3_LAB')
  AND eligible_for_training IS TRUE
  AND entry_timestamp >= :valid_from
  AND (
    -- (a) cobertura original: colunas dedicadas presentes.
    barrier_mode IS NULL OR tp_pct_applied IS NULL
    OR sl_pct_applied IS NULL OR barrier_contract_version IS NULL
    -- (b) Fase 1.5 P1 — coerência de VALOR (não só NOT NULL): uma linha
    -- ATR_DYNAMIC precisa carregar o contrato ATIVO (v2), não o carimbo v1
    -- do artefato TP-fixo. O valor esperado vem da config
    -- (ml_active_barrier_contract_version), nunca literal no SQL. Este é o
    -- mecanismo do v77: colunas setadas, mas com o contrato errado.
    OR (barrier_mode = 'ATR_DYNAMIC'
        AND barrier_contract_version IS DISTINCT FROM :active_contract_version)
    -- (c) sanidade física + clamps D4 (lidos de config, não literais).
    OR tp_pct_applied <= 0 OR sl_pct_applied <= 0
    OR tp_pct_applied < :clamp_min OR tp_pct_applied > :clamp_max
    OR sl_pct_applied < :clamp_min OR sl_pct_applied > :clamp_max
  )
""")

_CUMULATIVE_SQL = text("""
WITH mediana AS (
  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS med
  FROM (SELECT date_trunc('day', entry_timestamp) d, COUNT(*) n
        FROM shadow_trades
        WHERE source = :src AND barrier_mode = :bmode
          AND eligible_for_training IS TRUE AND outcome IS NOT NULL
          AND entry_timestamp >= :valid_from
          AND entry_timestamp < date_trunc('day', now())
        GROUP BY 1 ORDER BY 1 DESC LIMIT 7) t
)
SELECT COUNT(*) AS elegiveis_maturados_pos_boundary,
       (SELECT med FROM mediana) AS mediana_diaria_7d,
       -- Fase 1.3: metas config-driven (Zero Hardcode). milestone e retrain_gate
       -- vêm de config_profiles; a meta estendida saiu do display (decisão operador).
       CEIL(:milestone_rows::numeric / GREATEST(1, (SELECT med FROM mediana))) AS dias_para_milestone,
       CEIL(:retrain_rows::numeric / GREATEST(1, (SELECT med FROM mediana))) AS dias_para_retrain,
       now() AS calculado_em
FROM shadow_trades
WHERE source = :src AND barrier_mode = :bmode
  AND eligible_for_training IS TRUE AND outcome IS NOT NULL
  AND entry_timestamp >= :valid_from
""")

# WARN 1 — observação de produção (a correção do gate é contrato separado).
_WARN_GATE_BLOCKED_SQL = text("""
SELECT COUNT(*) AS n
FROM decisions_log
WHERE event_type = 'ML_GATE_BLOCKED'
  AND created_at >= now() - interval '2 hours'
  AND reason_codes ? 'NO_ELIGIBLE_MODEL_FOR_LANE'
""")

# WARN 2 — atr_pct_at_entry nulo em RUNNING é esperado para in-flight.
_WARN_ATR_RUNNING_SQL = text("""
SELECT COUNT(*) AS n
FROM shadow_trades
WHERE source = :src AND barrier_mode = :bmode
  AND entry_timestamp >= :w_from AND entry_timestamp < :w_to
  AND status = 'RUNNING' AND atr_pct_at_entry IS NULL
""")

_LAST_RUN_SQL = text("""
SELECT run_at, status, invariants
FROM ml_data_certification_runs
ORDER BY run_at DESC
LIMIT 1
""")

_YELLOW_TODAY_SQL = text("""
SELECT COUNT(*) AS n
FROM ml_data_certification_runs
WHERE status = 'YELLOW'
  AND run_at >= date_trunc('day', now())
""")

_INSERT_RUN_SQL = text("""
INSERT INTO ml_data_certification_runs (
    id, run_at, window_from, window_to, status, invariants, cumulative
) VALUES (
    gen_random_uuid(), now(), :window_from, :window_to, :status,
    CAST(:invariants AS JSONB), CAST(:cumulative AS JSONB)
)
RETURNING id, run_at
""")


async def _load_active_ml_config(db: AsyncSession) -> Dict[str, Any]:
    row = (await db.execute(text("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml' AND is_active = TRUE
        ORDER BY updated_at DESC
        LIMIT 1
    """))).fetchone()
    if not row or not row[0]:
        raise RuntimeError("missing_active_ml_config")
    payload = row[0]
    return payload if isinstance(payload, dict) else json.loads(payload)


def _require_generation_floor(ml_config: Dict[str, Any]) -> int:
    raw = ml_config.get("ml_certification_generation_floor")
    if raw is None:
        raise RuntimeError(
            "missing_ml_certification_generation_floor: gravar em "
            "config_profiles(config_type='ml') — decisão D3"
        )
    return int(raw)


def _require_str_config(ml_config: Dict[str, Any], key: str) -> str:
    """Read a required non-empty string from the active ML config (fail-closed)."""
    raw = ml_config.get(key)
    if raw is None or str(raw).strip() == "":
        raise RuntimeError(
            f"missing_{key}: gravar em config_profiles(config_type='ml')"
        )
    return str(raw)


def _require_float_config(ml_config: Dict[str, Any], key: str) -> float:
    """Read a required float from the active ML config (fail-closed)."""
    raw = ml_config.get(key)
    if raw is None:
        raise RuntimeError(
            f"missing_{key}: gravar em config_profiles(config_type='ml')"
        )
    return float(raw)


async def run_certification(
    db: AsyncSession,
    *,
    window_from: Optional[datetime] = None,
    window_to: Optional[datetime] = None,
    persist: bool = True,
    i09_informative: bool = False,
) -> Dict[str, Any]:
    """Roda a certificação; retorna o payload completo da execução.

    Sem janela explícita usa a janela do job: [now-26h, now]. ``persist=False``
    permite uso read-only (baseline/históricos) sem gravar linha.
    """
    now = datetime.now(timezone.utc)
    w_from = window_from or (now - timedelta(hours=JOB_WINDOW_HOURS))
    w_to = window_to or now

    ml_config = await _load_active_ml_config(db)
    valid_from = parse_required_ml_dataset_valid_from(ml_config)
    piso_d3 = _require_generation_floor(ml_config)
    alert_channel = str(ml_config.get("ml_certification_alert_channel") or "LOG_ONLY")
    # Fase 1.3 — metas do readiness lidas da config (Zero Hardcode), fail-closed
    # coerente com D3. Reutiliza o MESMO helper do gate de retrain
    # (ml_challenger_service._require_positive_int_config). Import local para não
    # arrastar libs de ML pesadas ao path do endpoint (latest_certification).
    try:
        from .ml_challenger_service import _require_positive_int_config
    except ImportError:  # pragma: no cover
        from backend.app.services.ml_challenger_service import (
            _require_positive_int_config,
        )
    milestone_rows = _require_positive_int_config(ml_config, "ml_readiness_milestone_rows")
    retrain_rows = _require_positive_int_config(ml_config, "ml_retrain_min_eligible_rows")
    # Fase 1.5 P1 — I12 coerência de valor: contrato ativo esperado e clamps D4,
    # todos lidos da config (fail-closed, nunca literal no SQL).
    active_contract_version = _require_str_config(
        ml_config, "ml_active_barrier_contract_version"
    )
    clamp_min = _require_float_config(ml_config, "shadow_barrier_min_pct")
    clamp_max = _require_float_config(ml_config, "shadow_barrier_max_pct")

    base_params = {
        "src": CANONICAL_SOURCE,
        "bmode": CANONICAL_BARRIER_MODE,
        "w_from": w_from,
        "w_to": w_to,
    }
    rows = (await db.execute(_INVARIANTS_SQL, {
        **base_params,
        "valid_from": valid_from,
        "piso_d3": piso_d3,
        "active_contract_version": active_contract_version,
        "clamp_min": clamp_min,
        "clamp_max": clamp_max,
    })).fetchall()
    invariants: List[Dict[str, Any]] = [
        {
            "invariante": r.invariante,
            "violacoes": int(r.violacoes),
            "status": r.status,
        }
        for r in rows
    ]

    cumulative_row = (await db.execute(_CUMULATIVE_SQL, {
        "src": CANONICAL_SOURCE,
        "bmode": CANONICAL_BARRIER_MODE,
        "valid_from": valid_from,
        "milestone_rows": milestone_rows,
        "retrain_rows": retrain_rows,
    })).mappings().one()
    cumulative = {
        "elegiveis_maturados_pos_boundary": int(
            cumulative_row["elegiveis_maturados_pos_boundary"]
        ),
        "mediana_diaria_7d": (
            float(cumulative_row["mediana_diaria_7d"])
            if cumulative_row["mediana_diaria_7d"] is not None else None
        ),
        "milestone_rows": milestone_rows,
        "dias_para_milestone": int(cumulative_row["dias_para_milestone"]),
        "retrain_gate_rows": retrain_rows,
        "dias_para_retrain": int(cumulative_row["dias_para_retrain"]),
        "calculado_em": cumulative_row["calculado_em"].isoformat(),
        "valid_from": valid_from.isoformat(),
    }

    warns: List[Dict[str, Any]] = []
    gate_blocked = (await db.execute(_WARN_GATE_BLOCKED_SQL)).scalar_one()
    if int(gate_blocked) > 0:
        warns.append({
            "warn": "ML_GATE_BLOCKED_NO_ELIGIBLE_MODEL_FOR_LANE_2H",
            "count": int(gate_blocked),
        })
    atr_running = (await db.execute(_WARN_ATR_RUNNING_SQL, base_params)).scalar_one()
    if int(atr_running) > 0:
        warns.append({
            "warn": "ATR_NULL_IN_RUNNING",
            "count": int(atr_running),
        })

    failed = [
        inv["invariante"] for inv in invariants
        if inv["status"] == "FAIL"
        and not (i09_informative and inv["invariante"].startswith("I09"))
    ]
    if failed:
        status = "RED"
    elif warns:
        status = "YELLOW"
    else:
        status = "GREEN"

    invariants_payload = {
        "invariants": invariants,
        "warns": warns,
        "failed": failed,
        "piso_d3": piso_d3,
        "i09_informative": i09_informative,
        "population": {
            "source": CANONICAL_SOURCE,
            "barrier_mode": CANONICAL_BARRIER_MODE,
        },
    }

    result: Dict[str, Any] = {
        "status": status,
        "window_from": w_from.isoformat(),
        "window_to": w_to.isoformat(),
        "invariants": invariants,
        "warns": warns,
        "failed": failed,
        "cumulative": cumulative,
        "persisted": False,
        "alerted": False,
        "alert_channel": alert_channel,
    }

    # Idempotência de alerta: mesma assinatura (status + violados) da execução
    # anterior recente ⇒ persiste a linha, mas não repete o alerta.
    previous = (await db.execute(_LAST_RUN_SQL)).fetchone()
    duplicate_alert = False
    if previous is not None and previous.status == status:
        prev_inv = previous.invariants
        if isinstance(prev_inv, str):
            prev_inv = json.loads(prev_inv)
        prev_failed = sorted((prev_inv or {}).get("failed") or [])
        prev_recent = (now - previous.run_at) <= timedelta(hours=2)
        duplicate_alert = prev_recent and prev_failed == sorted(failed)

    if persist:
        inserted = (await db.execute(_INSERT_RUN_SQL, {
            "window_from": w_from,
            "window_to": w_to,
            "status": status,
            "invariants": json.dumps(invariants_payload, default=str),
            "cumulative": json.dumps(cumulative, default=str),
        })).fetchone()
        await db.commit()
        result["persisted"] = True
        result["run_id"] = str(inserted.id)
        result["run_at"] = inserted.run_at.isoformat()

    # D2 = LOG_ONLY: RED imediato; YELLOW agregado em resumo diário; GREEN silencioso.
    if status == "RED" and not duplicate_alert:
        counts = {
            inv["invariante"]: inv["violacoes"]
            for inv in invariants if inv["invariante"] in failed
        }
        logger.error(
            "[ml-certification] RED invariantes_violados=%s contagens=%s "
            "janela=[%s, %s]",
            failed, counts, w_from.isoformat(), w_to.isoformat(),
        )
        result["alerted"] = True
    elif status == "YELLOW" and not duplicate_alert:
        yellow_today = int((await db.execute(_YELLOW_TODAY_SQL)).scalar_one())
        # A linha desta execução já foi persistida: 1 = primeira YELLOW do dia.
        if yellow_today <= 1:
            logger.warning(
                "[ml-certification] YELLOW (resumo diário) warns=%s janela=[%s, %s]",
                warns, w_from.isoformat(), w_to.isoformat(),
            )
            result["alerted"] = True
    elif status == "GREEN":
        logger.info(
            "[ml-certification] GREEN janela=[%s, %s] cumulativo=%d elegíveis",
            w_from.isoformat(), w_to.isoformat(),
            cumulative["elegiveis_maturados_pos_boundary"],
        )

    return result


async def latest_certification(db: AsyncSession) -> Optional[Dict[str, Any]]:
    """Última execução persistida — payload do endpoint /ml/readiness/latest."""
    row = (await db.execute(text("""
        SELECT id, run_at, window_from, window_to, status, invariants, cumulative
        FROM ml_data_certification_runs
        ORDER BY run_at DESC
        LIMIT 1
    """))).fetchone()
    if row is None:
        return None
    invariants = row.invariants
    cumulative = row.cumulative
    if isinstance(invariants, str):
        invariants = json.loads(invariants)
    if isinstance(cumulative, str):
        cumulative = json.loads(cumulative)
    return {
        "run_id": str(row.id),
        "run_at": row.run_at.isoformat(),
        "window_from": row.window_from.isoformat(),
        "window_to": row.window_to.isoformat(),
        "status": row.status,
        "invariants": (invariants or {}).get("invariants"),
        "warns": (invariants or {}).get("warns"),
        "failed": (invariants or {}).get("failed"),
        "cumulative": cumulative,
    }
