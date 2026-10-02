import test from 'node:test';
import assert from 'node:assert/strict';
import {latestRequestGuard,isIntelligenceStale,Intelligence} from './pump-intelligence';

test('late horizon or filter responses cannot overwrite newer refresh',()=>{
 const guard=latestRequestGuard();const old=guard.begin();const current=guard.begin();
 assert.equal(guard.isCurrent(old),false);assert.equal(guard.isCurrent(current),true);
 guard.invalidate();assert.equal(guard.isCurrent(current),false);
});
test('intelligence freshness uses its own aggregate and fetch timestamps',()=>{
 const now=Date.parse('2026-10-02T12:00:00Z');
 const data={computed_at:new Date(now-30000).toISOString(),freshness:{status:'current',refresh_failed:false},scope:{cache_seconds:60}} as Intelligence;
 assert.equal(isIntelligenceStale(data,now,now-1000),false);
 assert.equal(isIntelligenceStale(data,now+121000,now-1000),true);
 assert.equal(isIntelligenceStale({...data,freshness:{...data.freshness,refresh_failed:true}},now,now),true);
 assert.equal(isIntelligenceStale(null,now,null),true);
});
