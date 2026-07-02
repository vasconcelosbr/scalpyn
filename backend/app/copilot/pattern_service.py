from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .query_executor import QueryExecutor


class PatternService:
    def __init__(self, executor: QueryExecutor | None = None):
        self.executor = executor or QueryExecutor()

    async def discover(self, db: AsyncSession, user_id: UUID, analysis: str,
                       lookback_days: int, min_sample: int, session_id=None):
        params = {"lookback_days": lookback_days, "min_sample": min_sample, "user_id": str(user_id)}
        if analysis == "profile_performance":
            sql = """
                SELECT profile_id, profile_name, source, COUNT(*) AS sample_size,
                       AVG(CASE WHEN outcome IN ('TP', 'TP_HIT') THEN 1.0 ELSE 0.0 END) AS win_rate,
                       AVG(pnl_pct) AS avg_pnl_pct, AVG(mae_pct) AS avg_mae_pct, AVG(mfe_pct) AS avg_mfe_pct,
                       COUNT(*) FILTER (WHERE outcome IN ('TP', 'TP_HIT')) AS tp_count,
                       COUNT(*) FILTER (WHERE outcome IN ('SL', 'SL_HIT')) AS sl_count,
                       COUNT(*) FILTER (WHERE outcome = 'TIMEOUT') AS timeout_count
                FROM shadow_trades
                WHERE user_id = CAST(:user_id AS uuid) AND completed_at IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => :lookback_days)
                  AND profile_id IS NOT NULL
                GROUP BY profile_id, profile_name, source
                HAVING COUNT(*) >= :min_sample
                ORDER BY win_rate DESC, sample_size DESC
            """
        elif analysis == "indicator_performance":
            sql = """
                SELECT indicator, bucket_label, role_detected, total_cases AS sample_size,
                       win_rate, avg_pnl_pct, lift_vs_baseline, confidence_score,
                       false_positive_rate, false_negative_rate
                FROM profile_indicator_stats
                WHERE user_id = CAST(:user_id AS uuid) AND total_cases >= :min_sample
                  AND created_at >= NOW() - make_interval(days => :lookback_days)
                ORDER BY confidence_score DESC NULLS LAST, total_cases DESC
            """
        elif analysis == "period_comparison":
            sql = """
                SELECT profile_id, profile_name,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS n_7d,
                       AVG(CASE WHEN created_at >= NOW() - INTERVAL '7 days' AND outcome IN ('TP','TP_HIT') THEN 1.0
                                WHEN created_at >= NOW() - INTERVAL '7 days' THEN 0.0 END) AS win_rate_7d,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '14 days') AS n_14d,
                       AVG(CASE WHEN created_at >= NOW() - INTERVAL '14 days' AND outcome IN ('TP','TP_HIT') THEN 1.0
                                WHEN created_at >= NOW() - INTERVAL '14 days' THEN 0.0 END) AS win_rate_14d,
                       COUNT(*) AS n_period,
                       AVG(CASE WHEN outcome IN ('TP','TP_HIT') THEN 1.0 ELSE 0.0 END) AS win_rate_period
                FROM shadow_trades
                WHERE user_id = CAST(:user_id AS uuid) AND completed_at IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => :lookback_days)
                  AND profile_id IS NOT NULL
                GROUP BY profile_id, profile_name
                HAVING COUNT(*) >= :min_sample
                ORDER BY n_period DESC
            """
        else:
            raise ValueError("Análise não suportada")
        result = await self.executor.execute(
            db, user_id, sql, params, reason=f"Pattern discovery: {analysis}", session_id=session_id,
        )
        for row in result["rows"]:
            n = int(row.get("sample_size") or row.get("n_period") or 0)
            try:
                confidence = float(row["confidence_score"]) if row.get("confidence_score") is not None else None
            except (TypeError, ValueError):
                confidence = None
            if n < min_sample:
                strength = "amostra insuficiente"
            elif confidence is not None and confidence >= 0.8:
                strength = "padrão forte"
            elif confidence is not None and confidence >= 0.6:
                strength = "padrão moderado"
            else:
                strength = "padrão fraco"
            row["pattern_strength"] = strength
            row["statistical_warning"] = "risco de falso positivo estatístico" if n < max(min_sample * 2, 50) else None
        return {"analysis": analysis, "period_days": lookback_days,
                "min_sample": min_sample, "result": result}
