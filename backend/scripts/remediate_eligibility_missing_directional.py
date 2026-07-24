"""P0-C — Remediação de elegibilidade: linhas contaminadas sem bloco direcional.

Contexto: auditoria ``docs/audits/auditoria-captura-features-l3-2026-07-24.md``.
A regressão de captura do L3 (~17/jul/2026) gravou milhares de shadow trades com
``eligible_for_training=TRUE`` cujo ``features_snapshot`` NÃO contém o bloco
direcional. Como a flag antiga só validava ``atr_pct``, essas linhas passavam
como elegíveis e contaminavam o dataset de treino do ML com features NaN.

Este script aplica RETROATIVAMENTE a mesma regra que o P0-B passou a impor no
write-time (``feature_contract_v2.capture_native_snapshot``): uma linha sem o
bloco direcional NÃO é elegível para treino.

Princípio #11 do CLAUDE.md — ``features_snapshot`` é IMUTÁVEL após INSERT. Este
script NUNCA muta o snapshot; apenas corrige a coluna ``eligible_for_training``
(além de ``updated_at`` como carimbo de remediação). É estritamente conservador:
só DESLIGA a flag (demove) linhas contaminadas — nunca liga (não promove nada,
o que exigiria revalidar todo o contrato de linhagem).

Uso
---
Dry-run (padrão, read-only)::

    python backend/scripts/remediate_eligibility_missing_directional.py

Aplicar (idempotente)::

    python backend/scripts/remediate_eligibility_missing_directional.py --apply

Opções: ``--source L3`` para restringir; ``--report caminho.json`` para o output.
Fonte da URL: env ``DATABASE_PUBLIC_URL``/``DATABASE_URL`` ou Railway CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Fonte única de verdade: mesmo conjunto exigido pelo P0-B no write-time.
from app.ml.feature_contract_v2 import REQUIRED_DIRECTIONAL_FEATURES  # noqa: E402

DEFAULT_RAILWAY = r"C:\Users\ricar\.railway\bin\railway.exe"


def _db_url() -> str:
    env_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    railway = os.getenv("RAILWAY_BIN", DEFAULT_RAILWAY)
    proc = subprocess.run(
        [railway, "variables", "--service", "Postgres", "--environment", "production", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    data = json.loads(proc.stdout)
    url = data.get("DATABASE_PUBLIC_URL") or data.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_PUBLIC_URL/DATABASE_URL não encontrada")
    return url


def _has_directional_clause() -> str:
    """SQL booleano: TRUE quando TODAS as chaves do bloco direcional existem.

    Usa ``jsonb_exists`` (forma-função do operador ``?``) para evitar qualquer
    ambiguidade de paramstyle no driver.
    """
    return " AND ".join(
        f"jsonb_exists(features_snapshot, '{f}')" for f in REQUIRED_DIRECTIONAL_FEATURES
    )


def _diagnose(conn, has_dir: str, src_filter: str, params: dict) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            f"""
            SELECT source,
                   COUNT(*)                                                        AS total,
                   COUNT(*) FILTER (WHERE eligible_for_training)                    AS eligible,
                   COUNT(*) FILTER (WHERE eligible_for_training
                                      AND NOT ({has_dir}))                          AS to_demote,
                   COUNT(*) FILTER (WHERE eligible_for_training
                                      AND ({has_dir}))                             AS eligible_ok
              FROM shadow_trades
             WHERE TRUE {src_filter}
             GROUP BY source
             ORDER BY source
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="P0-C remediação de elegibilidade (bloco direcional ausente)")
    ap.add_argument("--apply", action="store_true", help="executa o UPDATE (padrão: dry-run read-only)")
    ap.add_argument("--source", default=None, help="restringe a um source (ex.: L3). Padrão: todas")
    ap.add_argument("--report", default=None, help="caminho do JSON de relatório")
    args = ap.parse_args()

    has_dir = _has_directional_clause()
    src_filter = "AND source = :source" if args.source else ""
    params = {"source": args.source} if args.source else {}

    engine = create_engine(_db_url())

    with engine.connect() as conn:
        before = _diagnose(conn, has_dir, src_filter, params)

    total_to_demote = sum(r["to_demote"] for r in before)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "dry_run",
        "source_filter": args.source,
        "required_directional_features": list(REQUIRED_DIRECTIONAL_FEATURES),
        "before": before,
        "total_to_demote": total_to_demote,
    }

    print(f"\n=== P0-C — remediação de elegibilidade ({report['mode'].upper()}) ===")
    print(f"Bloco direcional exigido ({len(REQUIRED_DIRECTIONAL_FEATURES)}): "
          f"{', '.join(REQUIRED_DIRECTIONAL_FEATURES)}\n")
    print(f"{'source':<14} {'total':>8} {'eligible':>10} {'to_demote':>11} {'elig_ok':>9}")
    for r in before:
        print(f"{r['source']:<14} {r['total']:>8} {r['eligible']:>10} {r['to_demote']:>11} {r['eligible_ok']:>9}")
    print(f"\nTOTAL a demover (eligible=TRUE sem bloco direcional): {total_to_demote}")

    if args.apply:
        if total_to_demote == 0:
            print("Nada a fazer — 0 linhas contaminadas.")
        else:
            with engine.begin() as conn:
                res = conn.execute(
                    text(
                        f"""
                        UPDATE shadow_trades
                           SET eligible_for_training = FALSE,
                               updated_at = NOW()
                         WHERE eligible_for_training = TRUE
                           AND NOT ({has_dir})
                           {src_filter}
                        """
                    ),
                    params,
                )
                report["rows_updated"] = res.rowcount
            print(f"\nAPPLIED — linhas atualizadas: {report['rows_updated']}")
            # Verificação PÓS (idempotência: deve zerar to_demote)
            with engine.connect() as conn:
                after = _diagnose(conn, has_dir, src_filter, params)
            report["after"] = after
            residual = sum(r["to_demote"] for r in after)
            report["residual_to_demote"] = residual
            print(f"Verificação PÓS — residual to_demote (deve ser 0): {residual}")
    else:
        print("\n[dry-run] Nenhuma alteração feita. Use --apply para executar.")

    out_path = Path(args.report) if args.report else (
        ROOT / ("p0c_remediation_applied.json" if args.apply else "p0c_remediation_dry_run.json")
    )
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nRelatório: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
