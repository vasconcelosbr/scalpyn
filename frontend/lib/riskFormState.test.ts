import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyRiskDraft,riskDraftReducer,sameRiskValues} from './riskFormState';
test('saved risk values hydrate directly without wrapper or UI defaults',()=>{
  const values={trailing_stop_enabled:true,take_profit_pct:1,trailing_stop_distance_pct:.7};
  assert.deepEqual(riskDraftReducer(emptyRiskDraft,{type:'load',values}).values,values);
});
test('revalidation cannot erase pending edits and failed readback cannot confirm save',()=>{
  let state=riskDraftReducer(emptyRiskDraft,{type:'load',values:{take_profit_pct:1}});
  state=riskDraftReducer(state,{type:'edit',key:'take_profit_pct',value:2});
  assert.equal(riskDraftReducer(state,{type:'load',values:{take_profit_pct:1}}),state);
  assert.equal(riskDraftReducer(state,{type:'saved',submitted:state.values,persisted:{take_profit_pct:1}}).dirty,true);
  assert.equal(riskDraftReducer(state,{type:'saved',submitted:state.values,persisted:{take_profit_pct:2}}).dirty,false);
});
test('old save response cannot overwrite a newer edit',()=>{
  const state={loaded:true,dirty:true,values:{take_profit_pct:3}};
  assert.equal(riskDraftReducer(state,{type:'saved',submitted:{take_profit_pct:2},persisted:{take_profit_pct:2}}),state);
  assert.equal(sameRiskValues({a:1,b:true},{b:true,a:1}),true);
});
