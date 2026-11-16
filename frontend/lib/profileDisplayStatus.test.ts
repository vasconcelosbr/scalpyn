import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import test from 'node:test';
import { WatchlistDecisionTable, type WatchlistDecisionItem } from '../components/watchlist/RejectedAssetTable';
import { watchlistDecisionRowKey } from './watchlistDecisionIdentity';

function item(status: WatchlistDecisionItem['status']): WatchlistDecisionItem {
  return { symbol:'BTC_USDT',status,stage:'L3',profile_id:'profile',data_reasons:['FEATURE_TTL_EXPIRED'],
    failed_indicators:[],conditions:[],current_values:{},expected_values:{},
    details:{filters:[],indicators:[],conditions:[],current_values:{},expected_values:{},evaluation_trace:[]} };
}
function render(value: WatchlistDecisionItem) {
  return renderToStaticMarkup(createElement(WatchlistDecisionTable, {items:[value],loading:false,
    expandedRow:watchlistDecisionRowKey(value),onExpandedRowChange:()=>{}}));
}
test('stale data renders its own state and is not counted as a rejection',()=>{
  const html=render(item('awaiting_data'));
  assert.match(html,/Aguardando dados/);
  assert.match(html,/Este ativo não foi rejeitado pela estratégia/);
  assert.match(html,/Leitura do indicador desatualizada/);
  assert.match(html,/Rejected<\/div><div[^>]*>0<\/div>/);
  assert.doesNotMatch(html,/Sem autorização de entrada confirmada/);
});
test('profile rejection does not invent a global flow restriction',()=>{
  const html=render(item('rejected'));
  assert.doesNotMatch(html,/Aguardando dados de fluxo atualizados|Sem autorização de entrada confirmada/);
  assert.match(html,/Rejected<\/div><div[^>]*>1<\/div>/);
});
