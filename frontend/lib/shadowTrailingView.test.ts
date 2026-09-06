import test from 'node:test';
import assert from 'node:assert/strict';
import { trailingSummary, type ShadowTrailingView } from './shadowTrailingView';
const view = (state:string,mode='APPLY',pct:number|null=null,remaining:number|null=null) => ({state,mode,floor:{pct},remaining_pp:remaining} as ShadowTrailingView);
test('active trailing shows protected floor and waiting shows distance',()=>{
  assert.match(trailingSummary(view('ACTIVE','APPLY',2.5)),/Trailing ativo.*Piso.*2,50%/);
  assert.match(trailingSummary(view('WAITING','APPLY',null,.4)),/Faltam.*0,40 p.p./);
});
test('observation and closed rows never advertise active protection',()=>{
  assert.equal(trailingSummary(view('OBSERVATION','OBSERVE',2.5)),'Observação');
  assert.equal(trailingSummary(view('CLOSED','APPLY',2.5)),'Encerrado');
  assert.equal(trailingSummary(view('UNAVAILABLE')),'Trailing não confirmado');
});
