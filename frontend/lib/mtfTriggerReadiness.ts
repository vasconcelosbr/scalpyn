import type { TriggerCheck } from '../components/mtf/types';

export function remainingToTrigger(row: TriggerCheck): { action: string; amount?: number } {
  if (row.state === 'PASS') return { action: 'Atendido' };
  if (row.state === 'DISABLED') return { action: 'Desativado' };
  if (row.state === 'UNAVAILABLE') return { action: 'Aguardar dados válidos' };
  if (row.state === 'WAITING') return { action: row.note || 'Aguardar a condição anterior' };
  if (row.id === 'previous_direction') return { action: 'Candle anterior já fechado; aguardar novo par de candles compatível' };
  const a = row.actual, b = row.expected;
  if (row.operator.startsWith('crosses_')) return { action: 'Confirmar cruzamento entre candles fechados; distância isolada não confirma' };
  if (typeof a === 'number' && Number.isFinite(a)) {
    if (row.operator === 'between' && Array.isArray(b) && b.length === 2 && b.every(Number.isFinite)) {
      return a < b[0] ? { action: 'Subir', amount: b[0] - a } : a > b[1] ? { action: 'Cair', amount: a - b[1] } : { action: 'Aguardar confirmação do motor' };
    }
    if (typeof b === 'number' && Number.isFinite(b)) {
      if (['>', '>='].includes(row.operator)) return a < b ? { action: row.operator === '>' ? 'Subir mais de' : 'Subir', amount: b-a } : { action: 'Superar o alvo (igualdade não basta)' };
      if (['<', '<='].includes(row.operator)) return a > b ? { action: row.operator === '<' ? 'Cair mais de' : 'Cair', amount: a-b } : { action: 'Ficar abaixo do alvo (igualdade não basta)' };
      if (['=', '=='].includes(row.operator)) return { action: a < b ? 'Subir até o alvo' : 'Cair até o alvo', amount: Math.abs(b-a) };
    }
  }
  return { action: 'Confirmar o estado exigido no alvo' };
}
