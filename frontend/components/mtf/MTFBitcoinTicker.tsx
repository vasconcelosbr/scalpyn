'use client';

import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Bitcoin, CircleHelp, Minus, MoveRight, TrendingDown, TrendingUp } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import type { Analysis } from './types';

type BitcoinQuote = {
  price: number | null;
  as_of: string | null;
  status: 'READY' | 'PARTIAL' | 'STALE' | 'UNAVAILABLE';
  changes: Record<'4h' | '1h' | '5m', { pct: number | null; reference_at: string | null }>;
};

export function MTFBitcoinTicker({ pollSeconds, refresh, timezone, analysis }: {
  pollSeconds?: number; refresh: number; timezone: string; analysis?: Analysis;
}) {
  const [quote, setQuote] = useState<BitcoinQuote | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!pollSeconds) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await apiFetch<BitcoinQuote>('/mtf/btc-market', { signal: controller.signal });
        if (!controller.signal.aborted) { setQuote(next); setFailed(false); }
      } catch {
        if (!controller.signal.aborted) { setQuote(null); setFailed(true); }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, pollSeconds * 1000);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [pollSeconds, refresh]);

  const time = (value: string) => new Intl.DateTimeFormat('pt-BR', {
    timeZone: timezone, hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value));
  const available = quote?.price != null && Number.isFinite(quote.price);
  const status = failed || quote?.status === 'UNAVAILABLE' ? 'Indisponível'
    : quote?.status === 'STALE' ? 'Sem dados recentes' : quote?.status === 'PARTIAL' ? 'Histórico parcial'
    : quote?.as_of ? `Consultado ${time(quote.as_of)}` : 'Carregando…';
  // MTF_BTC_VETO_READING: use the selected evaluation, never the live price return.
  const current = analysis && !analysis.vetoes.some(v => /^(DATA_STALE|EVALUATION_STALE|CONFIG_CHANGED)/.test(v))
    && analysis.trigger_diagnostics?.checks.find(c => c.id === 'evaluation_current')?.state !== 'FAIL';
  const trend = current ? analysis.btc_trend : null;
  const trendLabel = trend === 'UP' ? 'Alta' : trend === 'DOWN' ? 'Baixa' : trend === 'RANGE' ? 'Lateral' : 'Indisponível';
  const TrendIcon = trend === 'UP' ? TrendingUp : trend === 'DOWN' ? TrendingDown : trend === 'RANGE' ? MoveRight : CircleHelp;
  const trendClass = trend === 'UP' ? 'up' : trend === 'DOWN' ? 'down' : trend === 'RANGE' ? 'range' : 'unavailable';
  const trendHint = current && trendLabel !== 'Indisponível'
    ? `Estrutura de topos e fundos confirmados da Gate.io. Mesma leitura usada pelo filtro de tendência do Bitcoin na avaliação de ${analysis.symbol.replace('_', '/')} às ${time(analysis.observed_at_utc)}. Não é a variação percentual. Alta ou lateral não garantem um gatilho; os demais critérios continuam aplicáveis.`
    : 'Aguardando uma avaliação atual do ativo com o contexto estrutural do Bitcoin. A variação percentual não substitui essa leitura.';

  return <section className="mtf-btc-ticker" aria-label="Cotação do Bitcoin em USDT">
    <div className="mtf-btc-price">
      <Bitcoin size={20} aria-hidden="true"/>
      <div><span>BTC <small>USDT</small></span><strong>{available
        ? quote!.price!.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</strong></div>
    </div>
    <div className="mtf-btc-changes">{(['4h', '1h', '5m'] as const).map(tf => {
      const change = quote?.changes?.[tf];
      const value = available && change?.pct != null && Number.isFinite(change.pct) ? change.pct : null;
      const direction = value == null ? 'unavailable' : value > 0 ? 'up' : value < 0 ? 'down' : 'flat';
      const Arrow = direction === 'up' ? ArrowUp : direction === 'down' ? ArrowDown : Minus;
      const text = value == null ? '—' : `${value > 0 ? '+' : ''}${value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
      const label = tf === '5m' ? '5min' : tf.toUpperCase();
      const explanation = change?.reference_at && value != null
        ? `Variação desde o fechamento de 1min às ${time(change.reference_at)}. Gate.io BTC/USDT.`
        : 'Referência indisponível; nenhuma variação foi estimada.';
      return <div key={tf} className={`mtf-btc-change ${direction}`} title={explanation}
        aria-label={`${label}: ${direction === 'up' ? 'alta' : direction === 'down' ? 'queda' : direction === 'flat' ? 'sem variação' : 'indisponível'} ${text}`}>
        <small>{label}</small><span><Arrow size={13} aria-hidden="true"/>{text}</span>
      </div>;
    })}</div>
    <small className="mtf-btc-source" title="Cotação BTC/USDT da Gate.io. Variações móveis com referência no último fechamento de 1min anterior a cada intervalo.">
      Gate.io · {status}
    </small>
    <div className={`mtf-btc-trend ${trendClass}`} role="status"
      aria-label={`Tendência do Bitcoin · 4H: ${trendLabel}`} title={trendHint}>
      <span>Tendência do Bitcoin · 4H</span>
      <strong><TrendIcon size={16} aria-hidden="true"/>{trendLabel}</strong>
    </div>
  </section>;
}
