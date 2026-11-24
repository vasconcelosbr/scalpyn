"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
  type IChartApi, type ISeriesApi, type IPriceLine, type ISeriesMarkersPluginApi, type AutoscaleInfo,
  type Time,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ShadowTradeChartResponse } from "./types";
import { chartTickMarkFormatter, chartTimeFormatter, formatTime } from "@/lib/datetime";
import { ZonePrimitive } from '@/components/mtf/ZonePrimitive';
import { EntryBandPrimitive } from '@/components/mtf/EntryBandPrimitive';
import { MTFTriggerReadiness } from '@/components/mtf/MTFTriggerReadiness';
import { entryBandView, entryBandLabels, entryBandColors } from '@/lib/mtfEntryBand';
import { reasonLabel } from '@/components/mtf/types';
import { number, patternLabels, type ChartData, type Timeframe, type Preferences, type Presentation, visibleMarks } from '@/components/mtf/types';

const COLORS = {
  bg: "#090c12",
  grid: "rgba(139, 154, 183, 0.09)",
  text: "#7f8aa3",
  border: "rgba(139, 154, 183, 0.2)",
  green: "#18c98b",
  red: "#ff5263",
  blue: "#5a86ff",
  amber: "#f5aa42",
} as const;

function toUtcTimestamp(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function containingCandleTime(
  target: string,
  candleTimes: UTCTimestamp[],
): UTCTimestamp | null {
  if (candleTimes.length === 0) return null;
  const targetSeconds = toUtcTimestamp(target) as number;
  const prior = candleTimes.filter((candidate) => (candidate as number) <= targetSeconds);
  return prior.length > 0 ? prior[prior.length - 1] : candleTimes[0];
}

function exactTime(value: string): string {
  return formatTime(value, { hour12: false });
}

export function TradeCandlestickChart({ data }: { data: ShadowTradeChartResponse }) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  const candleData = useMemo(
    () =>
      data.candles
        .map((candle) => ({
          time: toUtcTimestamp(candle.time),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number)),
    [data.candles],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host || candleData.length === 0) return;

    const chart = createChart(host, {
      width: host.clientWidth,
      height: 480,
      layout: {
        background: { type: ColorType.Solid, color: COLORS.bg },
        textColor: COLORS.text,
        attributionLogo: true,
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      rightPriceScale: {
        borderColor: COLORS.border,
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      localization: { timeFormatter: chartTimeFormatter },
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 3,
        barSpacing: 9,
        tickMarkFormatter: chartTickMarkFormatter,
      },
      crosshair: {
        vertLine: { color: "rgba(90, 134, 255, 0.55)", labelBackgroundColor: COLORS.blue },
        horzLine: { color: "rgba(90, 134, 255, 0.4)", labelBackgroundColor: COLORS.blue },
      },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.green,
      downColor: COLORS.red,
      wickUpColor: COLORS.green,
      wickDownColor: COLORS.red,
      borderVisible: false,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    series.setData(candleData);

    const candleTimes = candleData.map((candle) => candle.time);
    const markers: SeriesMarker<UTCTimestamp>[] = [];
    const entryCandle = containingCandleTime(data.entry_timestamp, candleTimes);
    const exitCandle = containingCandleTime(data.exit_timestamp, candleTimes);
    const isProfitableExit = data.outcome === "TP_HIT" || (
      data.outcome === "TRAILING_STOP"
      && data.entry_price !== null
      && data.exit_price !== null
      && data.exit_price >= data.entry_price
    );
    const exitLabel = data.outcome === "TRAILING_STOP"
      ? "TRAILING"
      : data.outcome === "TP_HIT" ? "TP" : "SL";
    if (entryCandle !== null && data.entry_price !== null) {
      markers.push({
        id: "entry",
        time: entryCandle,
        position: "atPriceBottom",
        price: data.entry_price,
        shape: "arrowUp",
        color: COLORS.green,
        text: `B · COMPRA ${exactTime(data.entry_timestamp)}`,
        size: 2,
      });
    }
    if (exitCandle !== null && data.exit_price !== null) {
      markers.push({
        id: "exit",
        time: exitCandle,
        position: "atPriceTop",
        price: data.exit_price,
        shape: "arrowDown",
        color: isProfitableExit ? COLORS.green : COLORS.red,
        text: `S · ${exitLabel} ${exactTime(data.exit_timestamp)}`,
        size: 2,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(series, markers, { zOrder: "top" });

    const priceLines = [
      { price: data.entry_price, color: COLORS.blue, title: "ENTRADA", style: 2 as const },
      { price: data.tp_price, color: COLORS.green, title: "TP", style: 2 as const },
      { price: data.sl_price, color: COLORS.red, title: "SL", style: 2 as const },
      {
        price: data.exit_price,
        color: isProfitableExit ? COLORS.green : COLORS.red,
        title: "FECHAMENTO",
        style: 0 as const,
      },
    ];
    for (const line of priceLines) {
      if (line.price === null) continue;
      series.createPriceLine({
        price: line.price,
        color: line.color,
        lineWidth: line.title === "FECHAMENTO" ? 2 : 1,
        lineStyle: line.style,
        axisLabelVisible: true,
        title: line.title,
      });
    }

    chart.timeScale().fitContent();
    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    observer.observe(host);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candleData, data]);

  if (candleData.length === 0) {
    return (
      <div
        style={{
          minHeight: 300,
          display: "grid",
          placeItems: "center",
          color: COLORS.text,
          fontSize: 13,
          border: `1px dashed ${COLORS.border}`,
          borderRadius: 10,
        }}
      >
        Sem candles OHLCV disponíveis para esta janela.
      </div>
    );
  }

  return <div ref={hostRef} style={{ width: "100%", minHeight: 480 }} />;
}

const family = (name: string) => name.startsWith('di_') || name.startsWith('adx') ? 'adx' : name.split('_')[0];
const overlay = (name: string) => name==='target' || name.startsWith('ema') || name.startsWith('bb_') || name==='vwap' || name==='psar';

import { targetLabel, targetPoints } from '@/lib/mtfSettings';

/** MTF extension: one chart instance, independent data/layout/theme updates. */
export function MTFCandlestickChart({ data, timeframe, preferences, config, onPreference, targetEnabled = false, analysisCurrent = false }: {
  data: ChartData; timeframe: Timeframe; preferences: Preferences; config: Presentation;
  onPreference: (change: Partial<Preferences>) => void;
  targetEnabled?: boolean;
  analysisCurrent?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const lines = useRef(new Map<string, ISeriesApi<'Line'>>());
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const zoneRef = useRef<ZonePrimitive | null>(null);
  const entryBandRef = useRef<EntryBandPrimitive | null>(null);
  const [bandClock, setBandClock] = useState(Date.now);
  const [fitBand, setFitBand] = useState(false);
  const band = config.entry_band_enabled ? entryBandView(data.analysis, analysisCurrent, bandClock) : null;
  const bandAvailable = !!band && band.low != null && band.high != null && band.state !== 'UNAVAILABLE';
  useEffect(() => {
    setBandClock(Date.now());
    const expires = Date.parse(data.analysis.entry_candidate?.valid_until ?? '');
    if (!Number.isFinite(expires) || expires <= Date.now()) return;
    const timer = setTimeout(() => setBandClock(Date.now()), expires - Date.now());
    return () => clearTimeout(timer);
  }, [data.analysis.entry_candidate]);
  useEffect(() => { setFitBand(false); }, [data.symbol]);
  const priceLines = useRef<IPriceLine[]>([]);
  const layoutKey = useRef('');
  const context = useRef('');
  const paneHeights = useRef<number[]>([]);
  const [hover, setHover] = useState<number | null>(null);
  const [visibleRange, setVisibleRange] = useState<{from:number;to:number}|null>(null);
  const [height, setHeight] = useState(config.panel_heights.price);
  const frame = data.frames[timeframe];
  const target = useMemo(() => targetPoints(frame.candles, config.target_pct), [frame.candles, config.target_pct]);
  const targetTitle = config.target_pct == null ? 'Target' : targetLabel(config.target_pct);
  const showTarget = targetEnabled && preferences.show_target !== false && target.length > 0;
  const enabled = useMemo(()=>preferences.enabled_indicators ?? config.default_indicators,[preferences.enabled_indicators,config.default_indicators]);
  const collapsed = useMemo(()=>preferences.collapsed_panels ?? [],[preferences.collapsed_panels]);
  const light = preferences.theme === 'light';
  const date = useCallback((t: number) => new Intl.DateTimeFormat('pt-BR', {timeZone:preferences.timezone,day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(t*1000)),[preferences.timezone]);
  const formatPrice = useCallback((v: number) => data.price_format ? number(v,data.price_format.precision) : v.toLocaleString('pt-BR',{maximumSignificantDigits:16}),[data.price_format]);
  const clipping=useMemo(()=>{
    if(!preferences.clipped_scale) return null;
    const candles=frame.candles.filter(c=>!visibleRange || (c.time>=visibleRange.from && c.time<=visibleRange.to));
    if(!candles.length) return null;
    const lows=candles.map(c=>c.low).sort((a,b)=>a-b),highs=candles.map(c=>c.high).sort((a,b)=>a-b);
    const fraction=config.autoscale_clip_pct/100;
    const low=lows[Math.floor((lows.length-1)*fraction)],high=highs[Math.ceil((highs.length-1)*(1-fraction))];
    return {low,high,outside:candles.filter(c=>c.low<low || c.high>high)};
  },[preferences.clipped_scale,frame.candles,visibleRange,config.autoscale_clip_pct]);

  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {autoSize:true,layout:{attributionLogo:true,fontFamily:'"JetBrains Mono", monospace'},
      crosshair:{mode:CrosshairMode.Normal},timeScale:{timeVisible:true,secondsVisible:false}});
    chartRef.current = chart;
    const price = chart.addSeries(CandlestickSeries,{upColor:COLORS.green,downColor:COLORS.red,wickUpColor:COLORS.green,wickDownColor:COLORS.red,borderVisible:false});
    priceRef.current = price;
    markersRef.current = createSeriesMarkers(price,[]);
    const currentLines=lines.current;
    chart.subscribeCrosshairMove(e => setHover(e.time ? Number(e.time) : null));
    chart.timeScale().subscribeVisibleTimeRangeChange(range=>{
      const next=range?{from:Number(range.from),to:Number(range.to)}:null;
      setVisibleRange(old=>old?.from===next?.from && old?.to===next?.to?old:next);
    });
    return () => { chart.remove(); chartRef.current=null; priceRef.current=null; currentLines.clear(); volumeRef.current=null; layoutKey.current=''; context.current=''; };
  }, []);

  useEffect(() => {
    const chart=chartRef.current;
    if (!chart) return;
    chart.applyOptions({layout:{background:{type:ColorType.Solid,color:light?'#ffffff':COLORS.bg},textColor:light?'#526078':'#8c9ab0'},
      grid:{vertLines:{color:light?'#eef1f6':COLORS.grid},horzLines:{color:light?'#eef1f6':COLORS.grid}},
      localization:{timeFormatter:(t:Time)=>date(Number(t))},timeScale:{tickMarkFormatter:(t:Time)=>date(Number(t))}});
  }, [light, date]);

  useEffect(() => {
    const chart=chartRef.current, price=priceRef.current;
    if (!chart || !price) return;
    const names=Object.keys(frame.indicators).filter(n=>n!=='atr' && n!=='volume_ma' && enabled.includes(n) && (overlay(n)||!collapsed.includes(family(n))));
    if (showTarget) names.push('target');
    const key=JSON.stringify([names,collapsed,enabled.includes('volume_ma'),config.panel_heights]);
    const sameContext=context.current===`${data.symbol}:${timeframe}`;
    const range=sameContext?chart.timeScale().getVisibleLogicalRange():null;
    if (!sameContext && context.current) {
      // The library shares a time index across series. Empty the old timeline
      // before replacing its timeframe so markers/crosshair cannot resolve an
      // old candle index against the new candle set during synchronous updates.
      chart.clearCrosshairPosition();
      markersRef.current?.setMarkers([]);
      if (zoneRef.current) {price.detachPrimitive(zoneRef.current);zoneRef.current=null;}
      if (entryBandRef.current) {price.detachPrimitive(entryBandRef.current);entryBandRef.current=null;}
      lines.current.forEach(series=>series.setData([]));
      volumeRef.current?.setData([]);
      price.setData([]);
    }
    if (key!==layoutKey.current) {
      lines.current.forEach(s=>chart.removeSeries(s)); lines.current.clear();
      if (volumeRef.current) {chart.removeSeries(volumeRef.current);volumeRef.current=null;}
      const groups=new Map<string,number>();
      let pane=1;
      if (!collapsed.includes('volume')) {
        groups.set('volume',pane++);
        volumeRef.current=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false},groups.get('volume'));
        if (enabled.includes('volume_ma')) lines.current.set('volume_ma',chart.addSeries(LineSeries,{color:COLORS.blue,lineWidth:1,priceLineVisible:false,lastValueVisible:false,title:'Volume · média'},groups.get('volume')));
      }
      const colors=['#f5aa42','#5a86ff','#bb91ff','#2dd4bf','#ec8ba8','#92c964'];
      names.forEach((name,i)=>{
        if (!overlay(name) && !groups.has(family(name))) groups.set(family(name),pane++);
        lines.current.set(name,chart.addSeries(LineSeries,{color:name==='target'?'#38bdf8':colors[i%colors.length],lineWidth:1,title:name==='target'?targetTitle:name.toUpperCase(),priceLineVisible:false,lastValueVisible:name==='target'},overlay(name)?0:groups.get(family(name))));
      });
      paneHeights.current=[config.panel_heights.price];
      let total=config.panel_heights.price;
      groups.forEach((index,name)=>{const h=config.panel_heights[name]??config.panel_heights.other;paneHeights.current[index]=h;total+=h;});
      // setHeight redistributes the remaining panes on every call. Stretch
      // factors preserve the configured proportions when autoSize runs.
      chart.panes().forEach((pane,index)=>pane.setStretchFactor(paneHeights.current[index]));
      // Reserve separator and pixel-rounding space so no pane falls below its
      // configured minimum after the library rounds canvas dimensions.
      setHeight(total+chart.timeScale().height()+chart.panes().length*2);layoutKey.current=key;
    }
    price.applyOptions({priceFormat:data.price_format?{type:'price',precision:data.price_format.precision,minMove:data.price_format.min_move}:{type:'custom',formatter:formatPrice,minMove:Number.EPSILON},
      autoscaleInfoProvider:(base:()=>AutoscaleInfo|null)=>{
        const scale = clipping?{priceRange:{minValue:clipping.low,maxValue:clipping.high}}:base();
        if (scale?.priceRange && fitBand && bandAvailable) return {...scale,priceRange:{minValue:Math.min(scale.priceRange.minValue,band!.low!),maxValue:Math.max(scale.priceRange.maxValue,band!.high!)}};
        return scale;
      }});
    price.setData(frame.candles.map(c=>({...c,time:c.time as Time,...(c.closed?{}:{color:'#6b7280',wickColor:'#6b7280'})})));
    volumeRef.current?.setData(frame.candles.map(c=>({time:c.time as Time,value:c.volume,color:c.close>=c.open?'#18c98b60':'#ff526360'})));
    lines.current.forEach((series,name)=>{
      if (name==='target') series.applyOptions({title:targetTitle,priceFormat:data.price_format?{type:'price',precision:data.price_format.precision,minMove:data.price_format.min_move}:{type:'custom',formatter:formatPrice,minMove:Number.EPSILON}});
      series.setData((name==='target'?target:frame.indicators[name]??[]).map(p=>({...p,time:p.time as Time})));
    });
    markersRef.current?.setMarkers(visibleMarks(frame,config,preferences,timeframe===(data.analysis.stage_timeframes?.trigger ?? '5m')?data.analysis.trigger_5m:null).map(m=>({time:m.time as Time,text:m.label,position:m.above?'aboveBar':'belowBar',shape:'circle',color:m.pattern?(m.above?COLORS.red:COLORS.green):'#95a3b8'})));
    if (zoneRef.current) price.detachPrimitive(zoneRef.current);
    const reference=data.frames[data.analysis.stage_timeframes?.trigger ?? '5m'].candles.filter(c=>c.closed).at(-1)?.close;
    const zones=reference==null?[]:(frame.zones??[]);
    const below=zones.filter(z=>z.high<reference!).sort((a,b)=>b.high-a.high).slice(0,config.zones_per_side);
    const above=zones.filter(z=>z.low>reference!).sort((a,b)=>a.low-b.low).slice(0,config.zones_per_side);
    const inside=zones.filter(z=>z.low<=reference! && z.high>=reference!).slice(0,config.zones_per_side);
    zoneRef.current=new ZonePrimitive(preferences.show_zones===false?[]:[...below,...inside,...above]);price.attachPrimitive(zoneRef.current);
    if (entryBandRef.current) {price.detachPrimitive(entryBandRef.current);entryBandRef.current=null;}
    if (bandAvailable) {entryBandRef.current=new EntryBandPrimitive(band!.low!,band!.high!,entryBandColors[band!.state]);price.attachPrimitive(entryBandRef.current);}
    priceLines.current.forEach(line=>price.removePriceLine(line));priceLines.current=[];
    if (bandAvailable) for(const [title,value] of [['FAIXA CANDIDATA · MÍN',band!.low!],['FAIXA CANDIDATA · MÁX',band!.high!]] as const)
      priceLines.current.push(price.createPriceLine({price:value,color:entryBandColors[band!.state],lineWidth:1,lineStyle:2,axisLabelVisible:true,title}));
    if (band?.state==='CONFIRMED' && band.entry!=null)
      priceLines.current.push(price.createPriceLine({price:band.entry,color:entryBandColors.CONFIRMED,lineWidth:2,lineStyle:0,axisLabelVisible:true,title:'GATILHO CONFIRMADO · SHADOW'}));
    const plan=data.analysis.plan;
    if (plan) for(const [title,value,color] of [['ENTRADA TEÓRICA',plan.entry,COLORS.blue],['STOP SIMULADO',plan.stop,COLORS.red],['TP1',plan.tp1,COLORS.green],['TP2',plan.tp2,COLORS.green]] as const) {
      if (title==='ENTRADA TEÓRICA' && band?.state==='CONFIRMED') continue;
      priceLines.current.push(price.createPriceLine({price:value,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title}));
    }
    if (!sameContext) {const end=frame.candles.length-1;chart.timeScale().setVisibleLogicalRange({from:Math.max(0,end-config.visible_candles[timeframe]+1),to:end});setHover(null);}
    else if(range) chart.timeScale().setVisibleLogicalRange(range);
    context.current=`${data.symbol}:${timeframe}`;
  }, [data,timeframe,preferences,config,frame,enabled,collapsed,formatPrice,clipping,showTarget,target,targetTitle,bandClock,analysisCurrent,fitBand]);

  useEffect(()=>{
    const animation=requestAnimationFrame(()=>{
      const chart=chartRef.current;
      if(chart) setHeight(paneHeights.current.reduce((sum,h)=>sum+h,0)+chart.timeScale().height()+chart.panes().length*2);
    });
    return ()=>cancelAnimationFrame(animation);
  },[height]);

  const candle=frame.candles.find(c=>c.time===hover)??frame.candles.at(-1);
  const groups=[...new Set(['volume',...Object.keys(frame.indicators).filter(n=>n!=='atr'&&n!=='volume_ma'&&!overlay(n)).map(family)])];
  return <div className="mtf-chart-wrap">
    <div className="mtf-chart-heading"><strong>{data.symbol.replace('_','/')} <span>{timeframe.toUpperCase()}</span></strong><button onClick={()=>chartRef.current?.timeScale().scrollToRealTime()}>Último candle ↗</button></div>
    <div className="mtf-ohlc">{candle?`${date(candle.time)} · ${candle.closed?'fechado':'em formação'} · O ${formatPrice(candle.open)} H ${formatPrice(candle.high)} L ${formatPrice(candle.low)} C ${formatPrice(candle.close)} · Vol ${number(candle.volume,1)}`:'Aguardando candles'}</div>
    <div className="mtf-indicator-toggles">
      {targetEnabled && config.target_pct != null && <button className={preferences.show_target===false?'off':'mtf-target-toggle'} aria-pressed={preferences.show_target!==false} onClick={()=>onPreference({show_target:preferences.show_target===false})}>{targetTitle}</button>}
      {(['patterns','pivots','zones'] as const).map(key=>{const prop=`show_${key}` as 'show_patterns'|'show_pivots'|'show_zones';return <button key={key} aria-pressed={preferences[prop]!==false} className={preferences[prop]===false?'off':''} onClick={()=>onPreference({[prop]:preferences[prop]===false})}>{key==='patterns'?'Padrões':key==='pivots'?'Pivôs':'Zonas'}</button>;})}
      {Object.keys(frame.indicators).filter(n=>n!=='atr').map(name=><button key={name} aria-pressed={enabled.includes(name)} className={enabled.includes(name)?'':'off'} onClick={()=>onPreference({enabled_indicators:enabled.includes(name)?enabled.filter(n=>n!==name):[...enabled,name]})}>{name.toUpperCase()}</button>)}
    </div>
    <div className="mtf-panel-controls">{groups.map(group=><button key={group} aria-expanded={!collapsed.includes(group)} onClick={()=>onPreference({collapsed_panels:collapsed.includes(group)?collapsed.filter(g=>g!==group):[...collapsed,group]})}>{collapsed.includes(group)?'▸':'▾'} {group.toUpperCase()}</button>)}<button aria-pressed={!!preferences.clipped_scale} onClick={()=>onPreference({clipped_scale:!preferences.clipped_scale})}>{preferences.clipped_scale?'Restaurar escala completa':'Recortar extremos (visual)'}</button></div>
    {preferences.clipped_scale&&<div className="mtf-warning">Escala recortada apenas no desenho. Valores OHLC completos na legenda.
      {clipping?.outside.map(c=><div key={c.time}>{date(c.time)} · fora da escala: {c.low<clipping.low?`mínima ${formatPrice(c.low)}`:''} {c.high>clipping.high?`máxima ${formatPrice(c.high)}`:''}</div>)}
    </div>}
    {!data.price_format&&<div className="mtf-warning">Precisão do par indisponível; preços exibidos sem arredondamento por tick.</div>}
    {(frame.error||frame.warmup?.reason)&&<div className="mtf-warning">Histórico insuficiente ou descontínuo: indicadores sem cobertura permanecem indisponíveis.</div>}
    {frame.stale&&<div className="mtf-warning">Dados de mercado atrasados — sem confirmação de sinal atual.</div>}
    {config.entry_band_enabled && <section className={`mtf-entry-band ${band?.state ?? 'UNAVAILABLE'}`} aria-label="Faixa candidata de entrada" aria-live="polite">
      <div className="mtf-entry-band-heading"><strong>Faixa candidata · suporte {(band?.timeframe ?? '1h').toUpperCase()}</strong><span>{entryBandLabels[band?.state ?? 'UNAVAILABLE']}</span></div>
      {bandAvailable ? <>
        <div className="mtf-entry-band-prices"><b>{formatPrice(band!.low!)} — {formatPrice(band!.high!)}</b><button onClick={()=>setFitBand(!fitBand)} aria-pressed={fitBand}>{fitBand?'Restaurar enquadramento':'Enquadrar faixa'}</button></div>
        <p>Suporte: {formatPrice(band!.support_low!)} — {formatPrice(band!.support_high!)} · proximidade: {number(band!.proximity_atr,2)} × ATR do {(band?.timeframe ?? '1h').toUpperCase()}.</p>
        {band?.vetoes.map(v=><p key={v}>Veto: {reasonLabel(v,config)}.</p>)}
        {band?.state==='CONFIRMED' && band.entry!=null && <p>Entrada confirmada para simulação: {formatPrice(band.entry)}.</p>}
        <p>Faixa da avaliação de {date(Date.parse(band!.evaluated_at)/1000)}, válida até {date(Date.parse(band!.valid_until)/1000)}. Guia atual de proximidade; não representa entradas históricas nem aprovação de todos os preços da faixa.</p>
      </> : <p>{band?.reason==='NO_SUPPORT'?'Sem suporte confirmado selecionado pela análise.':'Sem avaliação atual válida para desenhar a faixa.'}</p>}
      <MTFTriggerReadiness analysis={data.analysis} current={analysisCurrent && bandAvailable} date={date}/>
    </section>}
    <div className="mtf-chart-scroll"><div ref={host} style={{width:'100%',height}} /></div>
    <div className="mtf-hover-detail">{showTarget && candle && <span className="mtf-target-value">{targetTitle} {formatPrice(target.find(p=>p.time===candle.time)?.value ?? NaN)} · </span>}{candle&&Object.entries(frame.indicators).flatMap(([name,points])=>{const point=points.find(p=>p.time===candle.time);return point?[`${name.toUpperCase()} ${number(point.value,4)}`]:[];}).join(' · ')}<br/>{frame.patterns.filter(p=>p.time===candle?.time).map(p=>patternLabels[p.pattern]??'Padrão').join(' · ')}</div>
    {targetEnabled && <div className="mtf-chart-note">{targetTitle}: valorização bruta sobre o fechamento de cada candle, antes de taxas. O ponto em formação acompanha o preço.</div>}
    <div className="mtf-chart-note">Zonas do {timeframe.toUpperCase()} · Contexto do plano: {(data.analysis.stage_timeframes?.zone ?? '1h').toUpperCase()} · Candle cinza em formação não gera sinal · {preferences.timezone==='UTC'?'UTC':'Brasília'}</div>
  </div>;
}
