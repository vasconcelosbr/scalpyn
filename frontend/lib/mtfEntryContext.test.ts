import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MTFEntryContext } from '../components/mtf/MTFEntryContext';
import type { Analysis, Presentation } from '../components/mtf/types';

const analysis = {
  status: 'NO_TRADE', config_hash: 'current', context_4h: { trend: 'UP' },
  vetoes: ['BTC_DOWNTREND'], trigger_5m: null, plan: null,
  trigger_diagnostics: { status: 'NO_TRADE', config_hash: 'current', checks: [
    { id: 'zone', label: 'Proximidade do suporte', timeframe: '1h', role: 'required', state: 'FAIL' },
    { id: 'trigger', label: 'Padrão de compra no candle fechado', timeframe: '5m', role: 'required', state: 'FAIL' },
    { id: 'volume', label: 'Volume relativo', role: 'score', state: 'FAIL' },
  ] },
} as Analysis;
const presentation = { reason_labels: { BTC_DOWNTREND: 'Bitcoin em tendência de baixa' } } as unknown as Presentation;
const render = (value?: Analysis) => renderToStaticMarkup(createElement(MTFEntryContext, { analysis: value, presentation }));

test('uptrend with veto remains no trade and shows unmet entry conditions without changing analysis', () => {
  const original = structuredClone(analysis);
  const html = render(analysis);
  for (const text of ['Viés estrutural', 'Alta', 'SEM TRADE', 'Bitcoin em tendência de baixa', 'Proximidade do suporte', 'Padrão de compra no candle fechado']) assert(html.includes(text));
  for (const text of ['COMPRA (Long)', 'GATILHO CONFIRMADO', 'Volume relativo']) assert(!html.includes(text));
  assert.deepEqual(analysis, original);
});

test('entry follows the evaluation status independently of structural direction', () => {
  for (const [status, text] of [['WAIT', 'AGUARDANDO'], ['ARMED', 'ARMADO · aguardando gatilho'], ['TRIGGERED', 'GATILHO CONFIRMADO']] as const) {
    const html = render({ ...analysis, status, vetoes: [], trigger_diagnostics: undefined });
    assert(html.includes(text));
    assert(!html.includes('SEM TRADE'));
    assert.equal(html.includes('Não representa uma ordem executada'), status === 'TRIGGERED');
  }
});

test('unavailable evaluations never suggest an entry and mismatched diagnostics are omitted', () => {
  const unavailable = render();
  assert(unavailable.includes('Indisponível'));
  assert(!unavailable.includes('GATILHO CONFIRMADO'));
  assert(!unavailable.includes('Alta'));
  const mismatch = render({ ...analysis, config_hash: 'changed' });
  assert(mismatch.includes('SEM TRADE'));
  assert(!mismatch.includes('Proximidade do suporte'));
});
