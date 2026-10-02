import test from 'node:test';
import assert from 'node:assert/strict';
import {latestRequestGuard,isIntelligenceStale,preferredCohort,Intelligence,Cohort,Support,TargetMetrics,completeTargetNarrative,executiveTime,targetNarrative,descriptiveComparison} from './pump-intelligence';

test('complete coverage counts are not mislabeled as a hit fraction',()=>{
 const narrative=(hits:number,known:number)=>completeTargetNarrative({complete_hits:hits,complete_known:known} as TargetMetrics);
 assert.equal(narrative(0,5),'Entre os 5 desfechos conhecidos com cobertura completa: 0 atingiram; 5 não atingiram.');
 assert.equal(narrative(2,5),'Entre os 5 desfechos conhecidos com cobertura completa: 2 atingiram; 3 não atingiram.');
 assert.equal(narrative(0,0),'Entre os 0 desfechos conhecidos com cobertura completa: 0 atingiram; 0 não atingiram.');
 // A hit from an incomplete path belongs to total known outcomes, not this subset.
 const partial={complete_hits:1,complete_known:2,hits:2,known:3,unknown:2} as TargetMetrics;
 assert.equal(completeTargetNarrative(partial),'Entre os 2 desfechos conhecidos com cobertura completa: 1 atingiram; 1 não atingiram.');
});

test('late horizon or filter responses cannot overwrite newer refresh',()=>{
 const guard=latestRequestGuard();const old=guard.begin();const current=guard.begin();
 assert.equal(guard.isCurrent(old),false);assert.equal(guard.isCurrent(current),true);
 guard.invalidate();assert.equal(guard.isCurrent(current),false);
});

test('executive summary states seven hits and eight unknown without operational percentage',()=>{
 const s={observations:15,targets:{'0.8':{hits:7,misses:0,known:7,unknown:8}}} as unknown as Support;
 const text=targetNarrative(s,'0.8');assert.match(text,/7 atingiram/);assert.match(text,/8 indeterminados/);
 assert.doesNotMatch(text,/%/);
});
test('winner concentration and group hit rate use different denominators',()=>{
 const s=(hits:number,known:number,observations:number)=>({observations,targets:{'0.8':{hits,known}}} as unknown as Support);
 const c=descriptiveComparison(s(2,4,10),s(8,20,30),'0.8');
 assert.equal(c.groupRate,.5);assert.equal(c.baselineRate,.4);assert.equal(c.winnerConcentration,.25);
 assert.equal(c.knownCoverage,.4);
 assert.equal(descriptiveComparison(s(0,0,5),s(0,0,10),'0.8').groupRate,null);
});
test('GMT minus three is fixed and crosses the calendar date correctly',()=>{
 const t=executiveTime('2026-01-01T02:30:00Z');assert.match(t,/31\/12\/2025/);assert.match(t,/23:30/);assert.match(t,/GMT−3/);
 assert.equal(executiveTime(null),'Sem dados');assert.equal(executiveTime('invalid'),'Sem dados');
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
