'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ExternalLink } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import type { Presentation } from './types';

type Sentiment = {
  value: number | null; classification: string | null; update_time: string | null; fetched_at: string | null;
  status: 'READY' | 'STALE' | 'UNAVAILABLE' | 'NOT_CONFIGURED' | 'DISABLED'; reason: string | null;
};
const labels: Record<string, { label: string; color: string }> = {
  'Extreme Fear': { label: 'Medo extremo', color: '#f36b7d' },
  Fear: { label: 'Medo', color: '#e69a39' }, Neutral: { label: 'Neutro', color: '#d1b534' },
  Greed: { label: 'Ganância', color: '#6abd52' }, 'Extreme Greed': { label: 'Ganância extrema', color: '#24c49a' },
};

export function MTFFearGreed({ config, refresh, timezone }: {
  config?: Presentation['fear_greed']; refresh: number; timezone: string;
}) {
  const [quote, setQuote] = useState<Sentiment | null>(null);
  const [failed, setFailed] = useState(false);
  const [expired, setExpired] = useState(false);
  const enabled = config?.enabled;
  const interval = config?.refresh_seconds;
  const maxAge = config?.max_age_seconds;
  useEffect(() => {
    if (!enabled || !interval) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await apiFetch<Sentiment>('/mtf/fear-greed', {signal: controller.signal});
        if (!controller.signal.aborted) { setQuote(value); setFailed(false); }
      } catch { if (!controller.signal.aborted) setFailed(true); }
      finally { if (!controller.signal.aborted) timer = setTimeout(poll, interval * 1000); }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [enabled, interval, refresh]);
  useEffect(() => {
    if (!quote?.update_time || !maxAge) { setExpired(false); return; }
    const remaining = Date.parse(quote.update_time) + maxAge * 1000 - Date.now();
    setExpired(remaining <= 0);
    if (remaining <= 0) return;
    const timer = setTimeout(() => setExpired(true), remaining);
    return () => clearTimeout(timer);
  }, [quote, maxAge]);
  if (!enabled) return null;
  const value = quote?.value;
  const available = value != null && Number.isFinite(value) && value >= 0 && value <= 100;
  const stale = available && (failed || expired || quote?.status !== 'READY');
  const state = quote?.classification ? labels[quote.classification] : null;
  const label = available && state ? state.label : 'Indisponível';
  const color = stale ? 'var(--mtf-muted)' : state?.color ?? 'var(--mtf-muted)';
  const angle = available ? Math.PI * (1 - value / 100) : 0;
  const date = (at: string) => new Intl.DateTimeFormat('pt-BR', {
    timeZone: timezone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(at));
  const needsKey = quote?.status === 'NOT_CONFIGURED' || quote?.reason === 'KEY_OR_PLAN_REJECTED';
  return <section className={`mtf-fear-greed${stale ? ' stale' : ''}`} aria-label="Fear & Greed da CoinMarketCap">
    <a className="mtf-fear-title" href="https://coinmarketcap.com/charts/fear-and-greed-index/" target="_blank" rel="noopener noreferrer">
      Fear &amp; Greed <ExternalLink size={12} aria-hidden="true"/>
    </a>
    <div className="mtf-fear-reading" role="status" aria-label={available ? `${label}: ${value}${stale ? ', desatualizado' : ''}` : needsKey ? 'Configure a CoinMarketCap' : failed || quote ? 'Indisponível' : 'Carregando'}>
      <div className="mtf-fear-gauge">
        <svg viewBox="0 0 120 66" aria-hidden="true">
          <path d="M12 58 A48 48 0 0 1 108 58" fill="none" stroke="var(--mtf-border)" strokeWidth="8"/>
          {['#f36b7d','#e69a39','#d1b534','#6abd52','#24c49a'].map((stroke,i) => {
            const a = Math.PI * (1 - i / 5), b = Math.PI * (1 - (i + 1) / 5) + .04;
            return <path key={stroke} d={`M${60+48*Math.cos(a)} ${58-48*Math.sin(a)} A48 48 0 0 1 ${60+48*Math.cos(b)} ${58-48*Math.sin(b)}`}
              fill="none" stroke={stale || !available ? 'var(--mtf-border)' : stroke} strokeWidth="7" strokeLinecap="round"/>;
          })}
          {available && <circle cx={60+48*Math.cos(angle)} cy={58-48*Math.sin(angle)} r="5" fill="var(--mtf-text)" stroke="var(--mtf-bg)" strokeWidth="2"/>}
        </svg>
        <strong>{available ? value : '—'}</strong>
      </div>
      <span style={{color}}>{label}</span>
    </div>
    {needsKey ? <Link className="mtf-fear-config" href="/settings/general">Configure a CoinMarketCap</Link>
      : <small className="mtf-fear-source">CoinMarketCap{stale ? ' · Desatualizado' : failed || quote?.status === 'UNAVAILABLE' ? ' · Indisponível' : quote ? '' : ' · Carregando…'}</small>}
    {available && quote?.update_time && <small className="mtf-fear-source" title={quote.fetched_at ? `Consultado em ${date(quote.fetched_at)}` : undefined}>Fonte: {date(quote.update_time)}</small>}
  </section>;
}
