"""Production release evidence. SELECTs and pure BlockEngine replays only."""
import asyncio
import json
import os
import asyncpg
from app.services.profile_runtime_config import canonical_hash, merge_profile_runtime_block_config
from app.services.block_engine import BlockEngine


def obj(value):
    return json.loads(value) if isinstance(value,str) else value or {}


async def main():
    db = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    result = {'mode':'READ_ONLY','profiles':[]}
    try:
        async with db.transaction(readonly=True):
            lists = await db.fetch("SELECT w.id,w.name,w.user_id,p.config FROM pipeline_watchlists w JOIN profiles p ON p.id=w.profile_id WHERE w.level='L3' AND p.is_active=true")
            for row in lists:
                profile = obj(row['config'])
                global_cfg = obj(await db.fetchval("SELECT config_json FROM config_profiles WHERE user_id=$1 AND config_type='block' AND is_active=true AND pool_id IS NULL ORDER BY updated_at DESC LIMIT 1",row['user_id']))
                effective = merge_profile_runtime_block_config(profile,global_cfg)
                original = (profile.get('entry_triggers') or {}).get('conditions',[])
                runtime = (effective.get('entry_triggers') or {}).get('conditions',[])
                result['profiles'].append({'watchlist':row['name'],'profile_hash':canonical_hash(profile),'block_hash':canonical_hash(global_cfg),
                    'canonical_conditions':original,'effective_conditions':runtime,'parity':original==runtime})
            result['legacy_shadow_count'] = await db.fetchval('SELECT count(*) FROM shadow_trades')
            result['legacy_rules_snapshots'] = [dict(r) for r in await db.fetch('SELECT id,rules_snapshot FROM shadow_trades ORDER BY created_at DESC LIMIT 3')]
            result['config_hashes'] = [dict(r) for r in await db.fetch("SELECT user_id,config_type,md5(config_json::text) AS hash FROM config_profiles WHERE is_active=true AND config_type NOT IN ('mtf_analysis','mtf_preferences') ORDER BY user_id,config_type")]
            result['recovery_observation_only'] = os.environ.get('RECOVERY_OBSERVATION_ONLY')
            result['bayesian_shadow_submission'] = os.environ.get('PROFILE_BAYESIAN_SHADOW_SUBMISSION_ENABLED')
            exists = await db.fetchval("SELECT to_regclass('public.mtf_chart_evaluations') IS NOT NULL")
            if exists:
                result['mtf_evaluations'] = await db.fetchval('SELECT count(*) FROM mtf_chart_evaluations')
                result['mtf_non_observational'] = await db.fetchval("SELECT count(*) FROM mtf_chart_evaluations WHERE payload->>'mode' <> 'SHADOW' OR (payload->>'operational_effect')::boolean IS NOT FALSE")
        # Independent required-trigger boundary replay, no exchange/order path.
        trigger = {'logic':'AND','conditions':[{'id':'mtf-release-required','indicator':'rsi','operator':'>=','value':50,'required':True,'enabled':True}]}
        engine = BlockEngine({'blocks':[], 'entry_triggers':trigger})
        result['boundary_replay'] = {'config':trigger,'below':engine.evaluate_entry({'rsi':49}),'above':engine.evaluate_entry({'rsi':51})}
        assert result['boundary_replay']['below']['allowed'] is False
        assert result['boundary_replay']['above']['allowed'] is True
        assert all(p['parity'] for p in result['profiles'])
        print('MTF_RELEASE_JSON='+json.dumps(result,default=str))
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
