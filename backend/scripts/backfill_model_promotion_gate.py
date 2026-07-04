#!/usr/bin/env python
"""Backfill — re-evaluate the Promotion Gate for every row in ml_models and
persist the result into metrics_json.promotion_gate.

Part of the Profile Intelligence Adaptive Loop reformulation (audit
2026-06-24, P0-1). Until this script runs, models created before the gate
existed (every model up to and including v47, in particular the two
currently `status='active'` — v44 CatBoost/L3_PROFILE and v46
LightGBM/L1_SPECTRUM) have no `metrics_json.promotion_gate` key at all, which
means the lane+gate eligibility filter added to prediction_service.py /
gcs_model_loader.py would treat them as ineligible by simple absence of data,
not because they were evaluated and rejected. This script makes the rejection
explicit and auditable.

Usage:
    python backfill_model_promotion_gate.py --dry-run            (default)
    python backfill_model_promotion_gate.py --commit
    python backfill_model_promotion_gate.py --commit --model-id <uuid>
    python backfill_model_promotion_gate.py --commit --limit 10

Never deletes or overwrites any column other than metrics_json (and even
there, only merges in the `promotion_gate` key — every other key already
present in metrics_json is preserved verbatim).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from app.ml.promotion_gate import evaluate_promotion_gate, merge_promotion_gate_into_metrics_json  # noqa: E402


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Defina DATABASE_PUBLIC_URL ou DATABASE_URL no ambiente antes de rodar este script."
        )
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    elif dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return dsn


def fetch_models(conn, model_id: str | None, limit: int | None):
    sql = """
        SELECT id, version, status, model_lane, label_version, source_filter,
               dataset_contract_id, feature_count, test_samples, roc_auc, metrics_json,
               train_from, train_to, dataset_query_cutoff, dataset_hash
        FROM ml_models
    """
    params: list = []
    if model_id:
        sql += " WHERE id = %s"
        params.append(model_id)
    sql += " ORDER BY created_at DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", default=True,
                         help="(default) compute and print, write nothing")
    parser.add_argument("--commit", action="store_true",
                         help="actually UPDATE ml_models.metrics_json")
    parser.add_argument("--model-id", default=None, help="restrict to a single model id")
    parser.add_argument("--limit", type=int, default=None, help="restrict to N most recent models")
    args = parser.parse_args()

    dry_run = not args.commit  # --commit is the only way to disable dry-run

    conn = psycopg2.connect(_get_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT config_json FROM config_profiles
                WHERE config_type = 'ml' AND is_active = true
                LIMIT 1
            """)
            cfg_row = cur.fetchone()
            promotion_config = dict(cfg_row["config_json"] or {}) if cfg_row else {}
        models = fetch_models(conn, args.model_id, args.limit)
        print(f"Encontrados {len(models)} modelo(s). dry_run={dry_run}\n")

        results = []
        for row in models:
            row = dict(row)
            gate_result = evaluate_promotion_gate(row, promotion_config=promotion_config)
            results.append({
                "id": str(row["id"]),
                "version": row["version"],
                "status": row["status"],
                "model_lane": row["model_lane"],
                "gate_status": gate_result["status"],
                "reasons": gate_result["reasons"],
            })

            marker = "  <<< ATIVO EM PRODUÇÃO" if row["status"] == "active" else ""
            print(
                f"v{row['version']:>3} | status={row['status']:<10} | "
                f"lane={str(row['model_lane']):<12} | gate={gate_result['status']:<9} | "
                f"reasons={gate_result['reasons']}{marker}"
            )

            if not dry_run:
                new_metrics_json = merge_promotion_gate_into_metrics_json(
                    row["metrics_json"], gate_result
                )
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ml_models SET metrics_json = %s WHERE id = %s",
                        (json.dumps(new_metrics_json), row["id"]),
                    )

        if not dry_run:
            conn.commit()
            print(f"\nCOMMITADO: {len(models)} linha(s) de ml_models.metrics_json atualizadas.")
        else:
            print(f"\nDRY-RUN: nenhuma escrita feita. Rode com --commit para persistir.")

        n_approved = sum(1 for r in results if r["gate_status"] == "APPROVED")
        n_rejected = sum(1 for r in results if r["gate_status"] == "REJECTED")
        n_blocked = sum(1 for r in results if r["gate_status"] == "BLOCKED")
        print(f"\nResumo: APPROVED={n_approved} REJECTED={n_rejected} BLOCKED={n_blocked}")

        active_results = [r for r in results if r["status"] == "active"]
        if active_results:
            print("\nModelos active avaliados:")
            for r in active_results:
                print(f"  v{r['version']} lane={r['model_lane']} -> {r['gate_status']} {r['reasons']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
