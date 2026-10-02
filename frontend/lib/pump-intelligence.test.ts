import test from 'node:test';
import assert from 'node:assert/strict';
import {latestRequestGuard,isIntelligenceStale,preferredCohort,Intelligence,Cohort} from './pump-intelligence';

test('late horizon or filter responses cannot overwrite newer refresh',()=>{
 const guard=latestRequestGuard();const old=guard.begin();const current=guard.begin();
 assert.equal(guard.isCurrent(old),false);assert.equal(guard.isCurrent(current),true);
 guard.invalidate();assert.equal(guard.isCurrent(current),false);
});
test('initial view finds mature V4 support instead of the newest empty cohort',()=>{
 const cohort=(id:string,version:string,known:number)=>({cohort_id:id,label_version:version,baseline:{observations:20,targets:{'0.8':{known}}}} as unknown as Cohort);
 const rows=[cohort('pending','pump_gross_touch_v4',0),cohort('old','pump_gross_touch_v3',20),cohort('mature','pump_gross_touch_v4',11)];
 assert.equal(preferredCohort(rows)?.cohort_id,'mature');assert.equal(preferredCohort([]),undefined);
 assert.equal(preferredCohort(rows.slice(0,2))?.cohort_id,'old');
});
test('intelligence historical snapshot does not expire merely because time passes',()=>{
 const now=Date.parse('2026-10-02T12:00:00Z');
 const data={computed_at:new Date(now-30000).toISOString(),freshness:{status:'current',refresh_failed:false},scope:{cache_seconds:60}} as Intelligence;
 assert.equal(isIntelligenceStale(data),false);
 assert.equal(isIntelligenceStale({...data,computed_at:new Date(now-3600000).toISOString()}),false);
 assert.equal(isIntelligenceStale({...data,freshness:{...data.freshness,refresh_failed:true}}),true);
 assert.equal(isIntelligenceStale(null),true);
 const feedStale={...data,data_through:new Date(now-180000).toISOString(),scope:{...data.scope,capture_freshness_seconds:120}};
 assert.equal(isIntelligenceStale(feedStale),false);
});
