"""Recovery sweep plus candle-triggered shadow continuation evaluation."""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, text

from .celery_app import celery_app

logger = logging.getLogger(__name__)


async def _sweep():
    from ..database import CeleryAsyncSessionLocal
    from ..models.shadow_trade import ShadowTrade
    from ..services.redis_client import get_async_redis
    from ..services.shadow_l3_flow_capture import WATCHED, drain
    from ..services.shadow_l3_exit_service import advance_shadow
    from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
    redis = await get_async_redis()
    async with CeleryAsyncSessionLocal() as db:
        configs = (await db.execute(text("""
            SELECT config_json FROM config_profiles
            WHERE config_type='shadow_l3_exit_policy' AND pool_id IS NULL AND is_active
        """))).scalars().all()
        policies = [ShadowL3ExitPolicy.model_validate(c) for c in configs] or [ShadowL3ExitPolicy()]
        batch_size = max(p.trade_batch_size for p in policies)
        # Recover only the recent rollout overlap. Future enrollment is atomic
        # with shadow creation, so discovery never scans historical JSONB.
        await db.execute(text("""
            WITH recent AS MATERIALIZED (
              SELECT id,user_id,source,config_snapshot FROM shadow_trades
              WHERE created_at >= now()-make_interval(secs=>:lookback)
            ) INSERT INTO shadow_l3_exit_states(shadow_id,user_id,policy_hash,policy)
            SELECT id,user_id,config_snapshot #>> '{shadow_l3_exit_policy,hash}',
                   config_snapshot->'shadow_l3_exit_policy'
            FROM recent WHERE source='L3' AND config_snapshot ? 'shadow_l3_exit_policy'
              AND config_snapshot #>> '{shadow_l3_exit_policy,config,mode}' <> 'LEGACY'
            ON CONFLICT DO NOTHING
        """), {"lookback":max(p.replay_lookback_seconds for p in policies)})
        ids = (await db.execute(text("""
            SELECT st.id,st.symbol FROM shadow_l3_exit_states es JOIN shadow_trades st ON st.id=es.shadow_id
            WHERE st.source='L3'
              AND es.policy #>> '{config,mode}' <> 'LEGACY'
              AND (es.state->>'outcome' IS NULL OR
                   (es.policy #>> '{config,mode}'='OBSERVE'
                    AND es.state->>'observation_complete' IS DISTINCT FROM 'true'))
            ORDER BY es.checked_at ASC NULLS FIRST,st.created_at ASC LIMIT :batch
        """), {"batch":batch_size})).all()
        if redis is not None:
            watched = (await db.execute(text("""
                SELECT DISTINCT st.symbol FROM shadow_l3_exit_states es JOIN shadow_trades st ON st.id=es.shadow_id
                WHERE st.source='L3'
                  AND es.policy #>> '{config,mode}' <> 'LEGACY'
                  AND (es.state->>'outcome' IS NULL OR
                       (es.policy #>> '{config,mode}'='OBSERVE'
                        AND es.state->>'observation_complete' IS DISTINCT FROM 'true'))
            """))).scalars().all()
            # Transaction makes replacement atomic for websocket readers.
            async with redis.pipeline(transaction=True) as pipe:
                pipe.delete(WATCHED)
                if watched: pipe.sadd(WATCHED, *watched)
                await pipe.execute()
            # drain commits. Deduped immutable trades make redelivery safe.
            await drain(db, redis, max(p.capture_batch_size for p in policies))
        processed = errors = 0
        for item in ids:
            try:
                async with db.begin_nested():
                    shadow = (await db.execute(select(ShadowTrade).where(ShadowTrade.id==item.id)
                                               .with_for_update(skip_locked=True))).scalar_one_or_none()
                    if shadow is None:
                        continue
                    snapshot = shadow.config_snapshot["shadow_l3_exit_policy"]["config"]
                    checked = (await db.execute(text("SELECT checked_at FROM shadow_l3_exit_states WHERE shadow_id=:id"), {"id":item.id})).scalar_one_or_none()
                    if checked and (datetime.now(timezone.utc)-checked).total_seconds() < snapshot["evaluation_seconds"]:
                        continue
                    await advance_shadow(db, shadow)
                    processed += 1
                await db.commit()
            except Exception:
                await db.rollback()
                errors += 1
                logger.exception("[shadow-l3-continuation] failed shadow=%s", item.id)
            finally:
                # Also release locks when the cadence check skips a row.
                await db.rollback()
        cutoff = datetime.now(timezone.utc)-timedelta(days=max(p.retention_days for p in policies))
        # Decision envelopes are permanent. Raw retention never removes inputs
        # still needed by an unfinished candidate or an open managed trade.
        await db.execute(text("""
            DELETE FROM shadow_l3_flow_trades WHERE ctid IN (
              SELECT f.ctid FROM shadow_l3_flow_trades f WHERE f.available_at < :cutoff
              AND NOT EXISTS (
                SELECT 1 FROM shadow_l3_exit_states es JOIN shadow_trades st ON st.id=es.shadow_id
                WHERE st.source='L3' AND st.symbol=f.symbol AND CASE WHEN COALESCE(st.exchange,'gate.io') IN ('gate','gateio') THEN 'gate.io' ELSE COALESCE(st.exchange,'gate.io') END=f.exchange AND f.market_type='spot'
                 
                  AND es.policy #>> '{config,mode}' <> 'LEGACY'
                  AND (es.state->>'outcome' IS NULL OR
                       (es.policy #>> '{config,mode}'='OBSERVE'
                        AND es.state->>'observation_complete' IS DISTINCT FROM 'true'))
                  AND f.occurred_at >= st.entry_timestamp - make_interval(secs=>COALESCE(
                    (es.policy #>> '{config,warmup_seconds}')::double precision,0))
              ) LIMIT :batch
            )
        """), {"cutoff":cutoff,"batch":max(p.capture_batch_size for p in policies)})
        await db.commit()
        return {"processed":processed,"errors":errors}


@celery_app.task(name="app.tasks.shadow_l3_continuation.run")
def run():
    from .shadow_trade_monitor import _run_async
    result = _run_async(_sweep())
    logger.info("[shadow-l3-continuation] %s", result)
    return result
