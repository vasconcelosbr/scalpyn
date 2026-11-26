'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Download, RefreshCw, Search, Database, ChevronDown } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatDateTime } from '@/lib/datetime';
import { Diagnostic, Json, diagnosticMetrics, exportDiagnostic, object, text } from '@/lib/watchlistDiagnostic';

const labels: Record<string, string> = {
  pipeline_asset: 'Registro do ativo na watchlist', pipeline_rejections: 'Rejeições e condições avaliadas',
  profile_classification: 'Classificação atual do perfil', profile_decision: 'Decisão e entradas registradas',
  market_metadata: 'Preço, volume e dados de mercado', indicators: 'Indicadores completos por produtor',
  mtf_observation: 'Snapshot MTF da watchlist', mtf_evaluation: 'Avaliação e auditoria MTF',
  consolidated_mtf_observation: 'Snapshot MTF do consolidado',
};
const date = (v: string | null | undefined) => v ? formatDateTime(v) : 'Horário não disponível';
const button = 'inline-flex items-center justify-center gap-2 rounded-lg border border-[#29354A] px-4 py-2.5 text-sm font-medium transition hover:bg-[#172235] focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 disabled:opacity-50';

function Evidence({ title, value, available = true }: { title: string; value: Json; available?: boolean }) {
  const [open, setOpen] = useState(false);
  return <section className="overflow-hidden rounded-xl border border-[#202A3B] bg-[#0C111A]">
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-3 px-5 py-4 text-left hover:bg-[#111B29]">
      <ChevronDown size={16} className={open ? 'rotate-180 text-blue-400' : 'text-slate-500'} />
      <span className="flex-1 text-sm font-medium text-slate-200">{title}</span>
      <span className={available ? 'text-xs text-emerald-400' : 'text-xs text-amber-400'}>{available ? 'Disponível' : 'Sem registro'}</span>
    </button>
    {open && <pre className="max-h-[520px] overflow-auto border-t border-[#202A3B] p-5 text-xs leading-relaxed text-slate-300">{JSON.stringify(value, null, 2)}</pre>}
  </section>;
}

export default function AssetDiagnosticPage() {
  const params = useParams<{ watchlistId: string; symbol: string }>();
  const [data, setData] = useState<Diagnostic | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [timeframe, setTimeframe] = useState('all');
  const [source, setSource] = useState('all');
  const request = useRef(0);
  const load = useCallback(async () => {
    const id = ++request.current;
    setLoading(true); setError(null); setData(null);
    try {
      const result = await apiFetch<Diagnostic>(`/watchlists/${encodeURIComponent(params.watchlistId)}/assets/${encodeURIComponent(params.symbol)}/diagnostic`);
      if (id === request.current) setData(result);
    }
    catch (e) { if (id === request.current) setError(e instanceof Error ? e.message : 'Não foi possível carregar o diagnóstico.'); }
    finally { if (id === request.current) setLoading(false); }
  }, [params.watchlistId, params.symbol]);
  useEffect(() => { setQuery(''); setTimeframe('all'); setSource('all'); void load(); return () => { request.current++; }; }, [load]);
  const metrics = useMemo(() => data ? diagnosticMetrics(data) : [], [data]);
  const filtered = useMemo(() => metrics.filter(m => (timeframe === 'all' || m.timeframe === timeframe) &&
    (source === 'all' || m.source === source) && m.name.toLowerCase().includes(query.toLowerCase())), [metrics, query, timeframe, source]);
  function download() {
    if (!data) return;
    const blob = new Blob([exportDiagnostic(data)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `scalpyn-${data.symbol}-${data.watchlist.level}-${data.captured_at.replace(/[:.]/g, '-')}.json`;
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const market = object(data?.sources.market_metadata);
  const asset = object(data?.sources.pipeline_asset);
  const classification = object(data?.sources.profile_classification);
  const snapshot = object(asset.analysis_snapshot);
  const decisionState = classification.status ?? snapshot.status ??
    (Array.isArray(data?.sources.pipeline_rejections) && data.sources.pipeline_rejections.length ? 'rejected' : data?.sources.pipeline_asset ? 'approved' : null);
  const price = asset.current_price ?? market.price;
  const stateLabels: Record<string, string> = { approved: 'Aprovado', rejected: 'Rejeitado', awaiting_data: 'Aguardando dados' };

  return <div className="mx-auto max-w-7xl space-y-7 pb-16 text-[#E2E8F0]">
    <Link href="/watchlist" className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-blue-300"><ArrowLeft size={16} /> Voltar à Watchlist</Link>
    <header className="flex flex-wrap items-start justify-between gap-5 border-b border-[#253044] pb-6">
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-blue-400">Diagnóstico do ativo</p>
        <h1 className="font-mono text-3xl font-semibold tracking-tight">{params.symbol}</h1>
        {data && <p className="mt-3 text-sm text-slate-400">{data.watchlist.level} · {data.watchlist.name} · {data.watchlist.market_mode.toUpperCase()}<span className="mx-2 text-slate-600">/</span>{data.profile?.name ?? 'Sem perfil associado'}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => void load()} disabled={loading} className={button}><RefreshCw size={15} className={loading ? 'animate-spin' : ''} />Atualizar leitura</button>
        <button type="button" onClick={download} disabled={!data || loading} className={button + ' border-blue-500/50 bg-blue-500/15 text-blue-200 hover:bg-blue-500/25'}><Download size={16} />Exportar JSON completo</button>
      </div>
    </header>
    {loading && <div role="status" className="rounded-xl border border-[#253044] p-10 text-center text-slate-400">Carregando indicadores e evidências do ativo…</div>}
    {error && <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/5 p-6 text-red-300">{error}</div>}
    {data && <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[['Classificação registrada', stateLabels[text(decisionState)] ?? 'Consulte as evidências'], ['Preço registrado', text(price)],
          ['Volume de mercado · 24h', text(asset.volume_24h ?? market.volume_24h)], ['Campos de indicadores', String(metrics.length)]].map(([label, value]) =>
          <div key={label} className="min-w-0 rounded-xl border border-[#253044] bg-[#0C111A] p-5"><p className="text-xs text-slate-400">{label}</p><p className="mt-3 break-words font-mono text-xl text-slate-100">{value}</p></div>)}
      </div>
      <div className="flex gap-3 rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 text-sm leading-relaxed text-slate-300">
        <Database size={18} className="mt-1 shrink-0 text-blue-400" />
        <div><p>Consulta de {date(data.captured_at)}. Cada fonte mantém seu próprio horário; uma leitura recente não substitui os dados usados na decisão.</p>
          <p className="mt-1 text-xs text-slate-400">Preço da watchlist: {date(typeof asset.refreshed_at === 'string' ? asset.refreshed_at : null)} · Mercado: {date(typeof market.last_updated === 'string' ? market.last_updated : null)}. A exportação inclui todos os campos recebidos, independentemente dos filtros abaixo.</p></div>
      </div>
      <section className="space-y-4">
        <div><h2 className="text-xl font-semibold">Indicadores captados e calculados</h2><p className="mt-1 text-sm text-slate-400">EMAs, volume, RSI, ADX, ATR e os demais campos disponíveis, com timeframe e origem.</p></div>
        <div className="flex flex-wrap gap-3">
          <label className="flex min-w-56 flex-1 items-center gap-2 rounded-lg border border-[#29354A] bg-[#0C111A] px-3"><Search size={16} className="text-slate-500" /><input aria-label="Buscar indicador" placeholder="Buscar EMA, RSI, ATR, volume…" value={query} onChange={e => setQuery(e.target.value)} className="w-full bg-transparent py-3 text-sm outline-none" /></label>
          <select aria-label="Timeframe" value={timeframe} onChange={e => setTimeframe(e.target.value)} className="rounded-lg border border-[#29354A] bg-[#0C111A] px-3 py-2 text-sm"><option value="all">Todos os timeframes</option>{Array.from(new Set(metrics.map(m => m.timeframe))).sort().map(tf => <option key={tf}>{tf}</option>)}</select>
          <select aria-label="Fonte dos indicadores" value={source} onChange={e => setSource(e.target.value)} className="max-w-full rounded-lg border border-[#29354A] bg-[#0C111A] px-3 py-2 text-sm"><option value="all">Todas as fontes</option>{Array.from(new Set(metrics.map(m => m.source))).map(s => <option key={s}>{s}</option>)}</select>
        </div>
        <div className="overflow-x-auto rounded-xl border border-[#253044]">
          <table className="w-full text-left text-sm"><thead className="bg-[#111925] text-xs uppercase tracking-wider text-slate-400"><tr>{['Indicador / campo', 'Valor', 'Timeframe', 'Origem', 'Horário da fonte'].map(label => <th key={label} className="px-4 py-3 font-medium">{label}</th>)}</tr></thead>
            <tbody>{filtered.map(m => <tr key={m.path} className="border-t border-[#1B2535] hover:bg-[#101927]"><td className="max-w-sm break-words px-4 py-3 font-mono text-xs text-blue-200" title={m.path}>{m.name}</td><td className="max-w-sm break-words px-4 py-3 font-mono text-xs">{text(m.value)}</td><td className="px-4 py-3 text-slate-300">{m.timeframe}</td><td className="px-4 py-3 text-xs text-slate-400">{m.source}</td><td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">{date(m.time)}</td></tr>)}
              {!filtered.length && <tr><td colSpan={5} className="p-8 text-center text-slate-400">{metrics.length ? 'Nenhum campo corresponde aos filtros.' : 'Não há indicadores persistidos disponíveis para este ativo.'}</td></tr>}
            </tbody></table>
        </div>
      </section>
      <section className="space-y-3">
        <h2 className="text-xl font-semibold">Decisão, contexto e dados completos</h2>
        <p className="pb-2 text-sm text-slate-400">Abra cada fonte para consultar todos os valores, regras, motivos e metadados registrados. Dados ausentes permanecem identificados.</p>
        {Object.entries(data.sources).map(([key, value]) => <Evidence key={key} title={labels[key] ?? key} value={value} available={data.availability[key] === 'available'} />)}
        <Evidence title="Configuração do perfil" value={data.profile as unknown as Json} available={!!data.profile} />
        <Evidence title="Configurações atuais dos indicadores e MTF" value={data.configuration} available={data.configuration.length > 0} />
      </section>
    </>}
  </div>;
}

