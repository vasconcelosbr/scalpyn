'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Activity, ArrowLeft, ChevronDown, ListFilter, Maximize2, Moon, PanelLeftClose, PanelRightClose, RefreshCw, Settings2, ShieldCheck, Sun, Volume2, VolumeX, X } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { MTFCandlestickChart } from '@/components/shadow-portfolio/TradeCandlestickChart';
import { number, patternLabels, patternShort, statusLabels, shadowLabels, readingLabel, mergeChart, reasonLabel, type Analysis, type Asset, type Bootstrap, type ChartData, type HistoryItem, type MTFConfig, type MTFReport, type Preferences, type Timeframe } from '@/components/mtf/types';
import './style.css';
import { MTFSettingsDialog } from '@/components/mtf/MTFSettingsDialog';
import { MTFPumpFlow } from '@/components/mtf/MTFPumpFlow';
import { MTFTriggerDiagnostics } from '@/components/mtf/MTFTriggerDiagnostics';
import { MTFBitcoinTicker } from '@/components/mtf/MTFBitcoinTicker';

const initialPreferences: Preferences = { theme: 'dark', timezone: 'America/Sao_Paulo', sound: false, aligned_only: false };
const statusOrder = { TRIGGERED: 0, ARMED: 1, WAIT: 2, NO_TRADE: 3 };
const labels: Record<string, string> = { trend: 'Estrutura 4H', force: 'Candle de força 4H', zone: 'Zona 1H', rejection: 'Rejeição 1H', trigger: 'Gatilho 5M', volume: 'Volume', rr: 'R:R líquido' };
function Badge({ analysis }: { analysis?: Analysis | null }) { return <span className={`mtf-badge ${analysis?.status ?? 'pending'}`}>{analysis ? statusLabels[analysis.status] : 'AGUARDANDO'}</span>; }
const bias = (value?: string) => value === 'UP' ? '▲ Alta' : value === 'DOWN' ? '▼ Baixa' : value === 'RANGE' ? '◆ Lateral' : '—';

export default function MTFPage() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [cfg, setCfg] = useState<MTFConfig | null>(null);
  const [prefs, setPrefs] = useState(initialPreferences);
  const [watchlist, setWatchlist] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [symbol, setSymbol] = useState('');
  const [profile, setProfile] = useState('');
  const [tf, setTf] = useState<Timeframe>('4h');
  const [data, setData] = useState<ChartData | null>(null);
  const [loadedContext, setLoadedContext] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [report, setReport] = useState<MTFReport | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [listError, setListError] = useState('');
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [configOpen, setConfigOpen] = useState(false);
  const [left, setLeft] = useState(true);
  const [right, setRight] = useState(true);
  const [full, setFull] = useState(false);
  const [scoreOrder, setScoreOrder] = useState(false);
  const [limited, setLimited] = useState(false);
  const [stale, setStale] = useState(false);
  const [membership, setMembership] = useState<'current'|'removed'>('current');
  const dataRef = useRef<ChartData | null>(null);
  const prefsRef = useRef(prefs);
  const preferenceWrite = useRef<Promise<unknown>>(Promise.resolve());
  const symbolRef = useRef(symbol);
  useEffect(()=>{prefsRef.current=prefs;},[prefs]);
  useEffect(()=>{symbolRef.current=symbol;},[symbol]);
  const audioRef = useRef<AudioContext | null>(null);
  const previousTriggers = useRef<Set<string> | null>(null);
  const requestContext = useRef('');

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<Bootstrap>('/mtf/bootstrap', { signal: controller.signal }).then(b => {
      setBoot(b); setCfg(b.config);
      let local = {};
      try { local = JSON.parse(localStorage.getItem('scalpyn-mtf-preferences') ?? '{}'); } catch { /* ignore damaged preferences */ }
      const saved={ ...initialPreferences, ...local, ...b.preferences };
      prefsRef.current=saved;setPrefs(saved);
      setWatchlist(b.watchlists.some(w=>w.id===saved.last_watchlist)?saved.last_watchlist!:b.config?.default_watchlist??b.preset.default_watchlist);
      if (saved.last_timeframe) setTf(saved.last_timeframe);
      if (!b.config?.presentation) setConfigOpen(true);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);

  const preference = (change: Partial<Preferences>) => {
    const next = { ...prefsRef.current, ...change };
    prefsRef.current=next;
    setPrefs(next);
    localStorage.setItem('scalpyn-mtf-preferences', JSON.stringify(next));
    // Preserve click order when consecutive saves have different latencies.
    preferenceWrite.current=preferenceWrite.current.catch(()=>undefined)
      .then(()=>apiFetch('/mtf/preferences', { method: 'PUT', body: JSON.stringify(next) }))
      .catch(() => { /* local fallback is intentional */ });
    if (change.sound) {
      audioRef.current ??= new AudioContext();
      void audioRef.current.resume();
    }
  };

  useEffect(() => {
    if (!cfg?.presentation || !watchlist) return;
    const presentation=cfg.presentation;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let first = true;
    let failures=0;
    let missingSymbol='';
    let missingCount=0;
    previousTriggers.current = null;
    const poll = async () => {
      if (first) setListLoading(true);
      try {
        const result = await apiFetch<{ items: Asset[]; limited: boolean }>(`/mtf/watchlist?watchlist_id=${encodeURIComponent(watchlist)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAssets(result.items); setLimited(result.limited); setListError('');
        failures=0;
        const currentSymbol=symbolRef.current;
        if (currentSymbol && !result.items.some(a=>a.symbol===currentSymbol)) {
          setMembership('removed');
          missingCount=missingSymbol===currentSymbol?missingCount+1:1;missingSymbol=currentSymbol;
          if (missingCount>=presentation.removal_confirmations) setSymbol(result.items[0]?.symbol??'');
        } else {
          missingCount=0;missingSymbol='';setMembership('current');
          if (!currentSymbol) setSymbol(result.items.find(a=>a.symbol===prefsRef.current.last_symbol)?.symbol??result.items[0]?.symbol??'');
        }
        const current = new Set(result.items.filter(a => a.mtf?.status === 'TRIGGERED').map(a => `${a.symbol}:${a.mtf?.candle_closed_5m}`));
        if (prefs.sound && previousTriggers.current && [...current].some(k => !previousTriggers.current!.has(k)) && audioRef.current) {
          const ac = audioRef.current;
          const oscillator = ac.createOscillator(); const gain = ac.createGain();
          oscillator.connect(gain); gain.connect(ac.destination); oscillator.frequency.value = 660;
          gain.gain.setValueAtTime(0.04, ac.currentTime); gain.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.3);
          oscillator.start(); oscillator.stop(ac.currentTime + 0.3);
        }
        previousTriggers.current = current;
        const report = await apiFetch<MTFReport>(`/mtf/report?watchlist_id=${encodeURIComponent(watchlist)}`, { signal: controller.signal });
        if (!controller.signal.aborted) setReport(report);
      } catch (e) { if (!controller.signal.aborted) {failures++;setListError(e instanceof Error ? e.message : 'Falha ao consultar watchlist');} }
      finally {
        first = false;
        if (!controller.signal.aborted) { setListLoading(false); timer = setTimeout(poll, (failures?Math.min(presentation.retry_max_seconds,presentation.retry_initial_seconds*2**(failures-1)):presentation.watchlist_poll_seconds) * 1000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [cfg, watchlist, refresh, prefs.sound]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let first = true;
    let failures=0;
    const key = `${watchlist}:${symbol}:${profile}`;
    const sameContext=requestContext.current===key;
    requestContext.current = key;
    const poll = async () => {
      if (!cfg || !watchlist || !symbol) return;
      if (first) setLoading(true);
      try {
        const query = new URLSearchParams({ watchlist_id: watchlist, symbol });
        if (profile) query.set('profile_id', profile);
        if (dataRef.current?.cursor) query.set('since',dataRef.current.cursor);
        const result = await apiFetch<ChartData>(`/mtf/chart?${query}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const merged=mergeChart(dataRef.current,result);
        dataRef.current=merged;setData(merged);setLoadedContext(key); setError('');setStale(false);setMembership('current');failures=0;
        const h = await apiFetch<{ items: HistoryItem[]; next_cursor: string | null }>(`/mtf/history?${new URLSearchParams({ watchlist_id: watchlist, symbol })}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setHistory(h.items); setCursor(h.next_cursor); }
      } catch (e) { if (!controller.signal.aborted) { failures++;setStale(true);
        const removed = e instanceof ApiError && e.status === 404 && e.detail === 'ASSET_NO_LONGER_IN_WATCHLIST';
        setError(removed ? '' : e instanceof Error ? e.message : 'Falha no gráfico');
        if (removed) setMembership('removed');
      } }
      finally {
        first = false;
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(poll, (failures&&cfg.presentation?Math.min(cfg.presentation.retry_max_seconds,cfg.presentation.retry_initial_seconds*2**(failures-1)):cfg.poll_seconds) * 1000); }
      }
    };
    const start = setTimeout(() => { if(!sameContext){dataRef.current=null;setData(null);setHistory([]);setCursor(null);setError('');setStale(false);} void poll(); }, 0);
    return () => { controller.abort(); clearTimeout(timer); clearTimeout(start); };
  }, [cfg, watchlist, symbol, profile, refresh]);

  const monitor = async () => {
    if (!cfg) return;
    const next = { ...cfg, monitored_watchlists: [...new Set([...cfg.monitored_watchlists, watchlist])] };
    try { await apiFetch('/mtf/config', { method: 'PUT', body: JSON.stringify(next) }); setCfg(next); }
    catch (e) { setError(e instanceof Error ? e.message : 'Falha ao ativar observação'); }
  };
  const moreHistory = async () => {
    if (!cursor) return;
    const context = requestContext.current;
    try {
      const h = await apiFetch<{ items: HistoryItem[]; next_cursor: string | null }>(`/mtf/history?${new URLSearchParams({ watchlist_id: watchlist, symbol, before: cursor })}`);
      if (context === requestContext.current) { setHistory(v => [...v, ...h.items]); setCursor(h.next_cursor); }
    } catch (e) { setError(e instanceof Error ? e.message : 'Falha no histórico'); }
  };
  const historical=stale || membership==='removed';
  const analysis = historical || loadedContext !== `${watchlist}:${symbol}:${profile}` ? undefined : data?.analysis;
  const plan = analysis?.plan;
  // Consolidated membership/status comes from the same observation as Aprovados;
  // selecting a symbol or an exploratory profile must not rewrite that snapshot.
  const shown = assets.map(a => listError ? {...a,mtf:undefined} : watchlist !== 'l3-consolidated' && a.symbol === data?.symbol ? { ...a, mtf: analysis } : a)
    .filter(a => !prefs.aligned_only || a.mtf?.status === 'ARMED' || a.mtf?.status === 'TRIGGERED')
    .sort((a,b) => (scoreOrder ? 0 : (statusOrder[a.mtf?.status ?? 'NO_TRADE'] - statusOrder[b.mtf?.status ?? 'NO_TRADE'])) || (scoreOrder ? (b.mtf?.score.raw ?? -1)-(a.mtf?.score.raw ?? -1) || (a.mtf?.zone_1h?.distance_atr ?? Infinity)-(b.mtf?.zone_1h?.distance_atr ?? Infinity) : (b.mtf?.score.total ?? -1)-(a.mtf?.score.total ?? -1)) || a.symbol.localeCompare(b.symbol));
  const date = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('pt-BR', { timeZone: prefs.timezone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : '—';
  const price = data?.frames[tf].candles.at(-1)?.close;
  const priceText=price==null?'—':data?.price_format?number(price,data.price_format.precision):price.toLocaleString('pt-BR',{maximumSignificantDigits:16});

  return <div className={`mtf-screen ${prefs.theme} ${full ? 'mtf-fullscreen' : ''}`}>
    <header className="mtf-header">
      <div className="mtf-brand"><Link href="/watchlist" title="Voltar à Watchlist"><ArrowLeft size={17}/></Link><Activity size={25}/><div><strong>Gráfico MTF</strong><small>SCALPYN / PRICE ACTION</small></div></div>
      <div className="mtf-symbol"><strong>{symbol.replace('_','/') || 'Selecione um ativo'}</strong><span>{priceText}</span></div>
      <div className="mtf-timeframes">{(['4h','1h','5m'] as Timeframe[]).map(t => <button key={t} className={t === tf ? 'active' : ''} onClick={() => {setTf(t);preference({last_timeframe:t});}}>{t.toUpperCase()}</button>)}</div>
      <label className="mtf-select"><span>Profile</span><select aria-label="Strategy Profile" value={profile} onChange={e => setProfile(e.target.value)}><option value="">Profile da watchlist</option>{boot?.profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.default_timeframe?.toUpperCase() ?? "TF não definido"} · {p.id.slice(0,6)}</option>)}</select></label>
      <MTFBitcoinTicker pollSeconds={cfg?.poll_seconds} refresh={refresh} timezone={prefs.timezone} analysis={analysis}/>
      <div className="mtf-header-tools"><span className="mtf-shadow"><ShieldCheck size={13}/> SHADOW</span><button aria-label="Alternar tema do ambiente MTF" title="Tema do ambiente MTF" onClick={() => preference({ theme: prefs.theme === 'dark' ? 'light' : 'dark' })}>{prefs.theme === 'dark' ? <Sun size={17}/> : <Moon size={17}/>}</button><button aria-label="Tela cheia" onClick={() => setFull(!full)}>{full ? <X size={17}/> : <Maximize2 size={17}/>}</button><button aria-label="Configurar análise MTF" onClick={() => setConfigOpen(true)}><Settings2 size={17}/></button></div>
    </header>
    <div className="mtf-context-bar">
      <div><small>4H · DIREÇÃO</small><strong className={analysis?.context_4h.trend === 'UP' ? 'positive' : ''}>{bias(analysis?.context_4h.trend)}</strong></div>
      <div><small>1H · ZONA</small><strong>{analysis?.zone_1h ? analysis.zone_1h.in_zone ? 'No suporte' : `${number(analysis.zone_1h.distance_atr,2)} ATR` : 'Aguardando zona'}</strong></div>
      <div><small>5M · GATILHO</small><strong>{analysis?.trigger_5m ? patternLabels[analysis.trigger_5m.pattern] : 'Sem gatilho'} {analysis?.trigger_5m?.volume_ratio != null && <em>· {number(analysis.trigger_5m.volume_ratio,1)}× vol</em>}</strong></div>
      <div><small>SCORE MTF · NÃO CALIBRADO</small><strong>{analysis ? `${number(analysis.score.total,1)} / ${number(analysis.score.maximum,1)}` : '—'}</strong></div>
      <Badge analysis={analysis}/><span className="mtf-asof">Mercado real · Simulação Shadow<br/>Candle {date(analysis?.candle_closed_5m)}–{date(analysis?.candle_close_at_utc)} (fechado)</span>
    </div>
    <div className="mtf-toolbar"><label className="mtf-select"><span>Watchlist</span><select aria-label="Watchlist" value={watchlist} onChange={e => { setWatchlist(e.target.value); preference({last_watchlist:e.target.value}); setAssets([]); setSymbol(''); setProfile(''); }}>{boot?.watchlists.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label><button onClick={() => setLeft(!left)}><PanelLeftClose size={14}/> Ativos</button><button onClick={() => setRight(!right)}><PanelRightClose size={14}/> Análise</button><Link href="/grafico-mtf/historico">Histórico de observações</Link><div className="mtf-spacer"/><button onClick={() => preference({ timezone: prefs.timezone === 'UTC' ? 'America/Sao_Paulo' : 'UTC' })}>{prefs.timezone === 'UTC' ? 'UTC' : 'Brasília'}</button><button aria-label="Alternar alerta sonoro" onClick={() => preference({ sound: !prefs.sound })}>{prefs.sound ? <Volume2 size={15}/> : <VolumeX size={15}/>}</button><button onClick={() => setRefresh(v => v+1)}><RefreshCw size={14} className={loading ? 'spin' : ''}/> Atualizar</button></div>
    {!cfg?.enabled && cfg && <div className="mtf-warning">Novas avaliações estão pausadas. O acompanhamento das simulações abertas continua.</div>}
    {cfg && !cfg.monitored_watchlists.includes(watchlist) && <div className="mtf-warning">Esta watchlist está em consulta. Para registrar avaliações continuamente: <button onClick={monitor}>Ativar observação Shadow</button></div>}
    <div className={`mtf-grid ${left ? '' : 'hide-left'} ${right ? '' : 'hide-right'}`}>
      {left && <aside className="mtf-assets mtf-card"><div className="mtf-card-title"><strong>{boot?.watchlists.find(w => w.id === watchlist)?.name}</strong><span>{assets.length} ativos</span></div><div className="mtf-asset-controls"><label><input type="checkbox" checked={prefs.aligned_only} onChange={e => preference({ aligned_only: e.target.checked })}/> Só alinhados</label><button onClick={() => setScoreOrder(!scoreOrder)}><ListFilter size={13}/>{scoreOrder ? 'Proximidade (bruto / zona)' : 'Status / score'}</button></div>
        {listError && <div className="mtf-error">{listError}</div>}{limited && <div className="mtf-warning">Lista limitada pela configuração.</div>}
        <div className="mtf-asset-head"><span>ATIVO</span><span>4H / ZONA 1H</span><span>SCORE</span></div>
        {shown.map(asset => <button className={`mtf-asset ${symbol === asset.symbol ? 'selected' : ''}`} key={asset.symbol} onClick={() => {setSymbol(asset.symbol);setRight(true);preference({last_symbol:asset.symbol});}}><div><strong>{asset.symbol.replace('_','/')}</strong><small>{asset.current_price?.toLocaleString("pt-BR",{maximumSignificantDigits:16}) ?? "—"}</small><small>{asset.price_snapshot_at ? date(asset.price_snapshot_at) : 'Horário indisponível'}</small><span>{asset.mtf?.trigger_5m ? patternShort[asset.mtf.trigger_5m.pattern] : '—'} · {date(asset.mtf?.candle_close_at_utc)}</span></div><div><span className={asset.mtf?.context_4h.trend === 'UP' ? 'positive' : ''}>{bias(asset.mtf?.context_4h.trend)}</span><small>{asset.mtf?.zone_1h ? `${number(asset.mtf.zone_1h.distance_atr,1)} ATR` : '—'}</small><Badge analysis={asset.mtf}/></div><b>{asset.mtf ? number(asset.mtf.score.total,1) : '—'}{asset.mtf?.vetoes.length ? <small>bruto {number(asset.mtf.score.raw,1)}</small> : null}</b></button>)}
        {!shown.length && <div className="mtf-empty">{listLoading ? 'Consultando oportunidades…' : prefs.aligned_only ? 'Nenhum ativo alinhado neste momento.' : 'Nenhuma oportunidade nesta watchlist. Selecione outra lista para explorar.'}</div>}
        <footer>Preços: snapshot da watchlist; horário de atualização quando disponível. Elegibilidade conforme os filtros habilitados em Vetos. Score MTF independente do pipeline.</footer>
        <section className="mtf-card"><div className="mtf-card-title"><strong>Expectativa · Shadow MTF</strong></div><div className="mtf-panel-body">{report?.groups.length ? report.groups.map(group => <div className="mtf-reading" key={`${group.pattern}:${group.config_hash}`}><strong>{patternLabels[group.pattern] ?? group.pattern} · 5M</strong><p>Amostra: {group.n} · Média líquida: {number(group.mean_pct,3)}%</p><p>{group.confidence_interval ? `IC ${number(report.confidence_level*100,0)}%: ${number(group.confidence_interval[0],3)}% a ${number(group.confidence_interval[1],3)}%` : 'Amostra insuficiente para intervalo de confiança.'}</p><small>Taxa por lado: {number(group.fee_per_side_pct,3)}% · Slippage: {number(group.slippage_pct,3)}% · Ambíguos: {group.ambiguous}</small></div>) : <p className="mtf-muted">Ainda não há resultados encerrados para estimar expectativa.</p>}<p className="mtf-muted">Apuração por versão de configuração. Intervalos descritivos; observações sobrepostas não são independentes. Sem calibração ou promoção automática.</p></div></section>
      </aside>}
      <section className="mtf-center mtf-card">
        {error && <div className="mtf-error">{error}<button onClick={() => setRefresh(v=>v+1)}>Tentar novamente</button></div>}
        {membership==='removed' && <div className="mtf-warning">Ativo saiu da watchlist.{data && ' Dados anteriores disponíveis apenas para consulta histórica.'}</div>}
        {loading && !data && <div className="mtf-empty mtf-loading"><RefreshCw className="spin"/> Carregando candles e indicadores canônicos…</div>}
        {data && cfg?.presentation && <><div className="mtf-data-freshness">{historical?`CONSULTA HISTÓRICA · dados de ${date(data.as_of)} · sem confirmação de oportunidade atual`:`Dados consultados em ${date(data.as_of)}`}</div><MTFCandlestickChart data={data} timeframe={tf} preferences={prefs} config={cfg.presentation} onPreference={preference} targetEnabled={watchlist==='l3-consolidated'}/></>}
        {!data && !loading && !error && membership!=='removed' && <div className="mtf-empty mtf-welcome"><Activity size={42}/><h2>O contexto antes do gatilho.</h2><p>Selecione uma oportunidade para conectar direção, zona e price action.</p><span>4H DIREÇÃO → 1H ZONA → 5M GATILHO</span></div>}
      </section>
      {right && <aside className="mtf-analysis">
        {cfg?.presentation && <MTFPumpFlow key={`flow:${watchlist}:${symbol}:${profile}`} context={analysis?.pump_flow} watchlist={watchlist} pollSeconds={cfg.presentation.watchlist_poll_seconds} timezone={prefs.timezone} stale={historical} />}
        <MTFTriggerDiagnostics key={`${watchlist}:${symbol}:${profile}`} analysis={analysis} symbol={symbol} date={date}
          pending={historical ? 'Avaliação indisponível ou desatualizada. Aguardando atualização.' : undefined}/>
        <section className="mtf-card"><div className="mtf-card-title"><strong>Plano simulado · 5M</strong><Badge analysis={analysis}/></div><div className="mtf-panel-body">
          <div className="mtf-data-row"><span>Viés Spot</span><b className={analysis?.context_4h.trend === 'UP' ? 'positive' : ''}>{analysis?.context_4h.trend === 'UP' ? 'COMPRA (Long)' : 'Não comprar'}</b></div>
          <div className="mtf-data-row"><span>Gatilho</span><b>{analysis?.trigger_5m ? patternLabels[analysis.trigger_5m.pattern] : '—'}</b></div>
          {([['Entrada teórica',plan?.entry,'accent'],['Stop simulado',plan?.stop,'negative'],['TP1 estrutural',plan?.tp1,'positive'],['TP2 estrutural',plan?.tp2,'positive'],['R:R líquido',plan?.rr_net,'positive'],['Posição · USDT',plan?.notional_usdt,'']] as const).map(([label,value,color]) => <div className="mtf-data-row" key={label}><span>{label}</span><b className={color}>{number(value)}</b></div>)}
          <div className="mtf-data-row"><span>Validade da entrada</span><b>{date(plan?.expires_at)}</b></div>
          <p className="mtf-muted">Entrada teórica no fechamento. Preenchimento Shadow somente com dados posteriores à avaliação. Custos e capital são hipóteses do preset.</p>
        </div></section>
        <section className="mtf-card"><div className="mtf-card-title"><strong>Confiança do setup</strong><span>{analysis ? `${number(analysis.score.total,1)}/${number(analysis.score.maximum,1)}` : '—'}</span></div><div className="mtf-panel-body">{Object.entries(analysis?.score.components ?? {}).map(([key,value]) => <div className="mtf-score-row" key={key}><span>{labels[key] ?? key}</span><b>{number(value,1)}</b><div><i style={{ width: `${Math.min(100, value / Math.max(1, Number((cfg?.score_weights as Record<string,number> | undefined)?.[key] ?? value)) * 100)}%` }}/></div></div>)}<p className="mtf-muted">Score bruto: {number(analysis?.score.raw,1)} · anterior aos vetos, sem autorização de entrada</p><h4>VETOS</h4>{analysis?.vetoes.length ? analysis.vetoes.map(v => <p className="negative mtf-veto" key={v}>{reasonLabel(v,cfg?.presentation)}</p>) : <p className="mtf-muted">{analysis ? 'Nenhum veto ativo' : 'Aguardando avaliação'}</p>}<p className="mtf-muted">Rubrica determinística, experimental e não calibrada.</p></div></section>
        <section className="mtf-card"><div className="mtf-card-title"><strong>Leitura multiframe</strong></div><div className="mtf-panel-body">{analysis?.explanation.map((line,i) => <p className="mtf-reading" key={i}>{readingLabel(line)}</p>)}{analysis?.unavailable_profile_indicators.length ? <p className="mtf-warning">Série histórica indisponível: {analysis.unavailable_profile_indicators.join(', ')}</p> : null}<p className="mtf-muted">Invalidação: rompimento do stop ou perda da zona no fechamento. Valores ausentes não são estimados.</p></div></section>
        <section className="mtf-card"><div className="mtf-card-title"><strong>Histórico · Shadow MTF</strong></div><div className="mtf-panel-body">{history.length ? history.map((h,i) => <div className="mtf-history-row" key={h.evaluation.evaluation_id ?? i}><small>{date(h.observed_at)}</small><span>{h.evaluation.trigger_5m ? patternLabels[h.evaluation.trigger_5m.pattern] : statusLabels[h.evaluation.status]}<small>{h.shadow ? shadowLabels[h.shadow.status] ?? 'Estado indisponível' : 'Avaliação'}{h.shadow?.ambiguous ? ' · intrabar ambíguo' : ''}</small></span><b className={(h.shadow?.pnl_pct ?? 0) < 0 ? 'negative' : 'positive'}>{h.shadow?.pnl_pct == null ? '—' : `${number(h.shadow.pnl_pct,2)}%`}</b></div>) : <p className="mtf-muted">Sem avaliações registradas para este contexto. A observação ocorre no backend após o fechamento dos candles.</p>}{cursor && <button className="mtf-more" onClick={moreHistory}>Carregar anteriores <ChevronDown size={13}/></button>}<p className="mtf-muted">Histórico separado do Shadow Portfolio operacional. Resultados líquidos dos custos configurados.</p></div></section>
      </aside>}
    </div>
    {configOpen && boot && <MTFSettingsDialog config={cfg} boot={boot} onClose={()=>setConfigOpen(false)} onSaved={next=>{setLoadedContext('');setCfg(next);setConfigOpen(false);if(!watchlist)setWatchlist(next.default_watchlist);}}/>}
  </div>;
}
