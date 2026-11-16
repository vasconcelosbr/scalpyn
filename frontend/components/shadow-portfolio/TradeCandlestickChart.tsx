"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
  type IChartApi,
  type Time,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ShadowTradeChartResponse } from "./types";
import { chartTickMarkFormatter, chartTimeFormatter, formatTime } from "@/lib/datetime";
import { ZonePrimitive } from '@/components/mtf/ZonePrimitive';
import { number, patternShort, patternLabels, type ChartData, type Timeframe, type Preferences } from '@/components/mtf/types';

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

/** MTF extension of the existing Shadow candlestick renderer. */
export function MTFCandlestickChart({ data, timeframe, preferences }: { data: ChartData; timeframe: Timeframe; preferences: Preferences }) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [legend, setLegend] = useState<string>('');
  const [hoverDetail, setHoverDetail] = useState<string>('');
  const [disabled, setDisabled] = useState<string[]>([]);
  const frame = data.frames[timeframe];
  const light = preferences.theme === 'light';
  const timeLabel = (seconds: number) => new Intl.DateTimeFormat('pt-BR', { timeZone: preferences.timezone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(seconds * 1000));
  const visibleRange = useRef<{ from: Time; to: Time } | null>(null);
  const context = useRef('');
  useEffect(() => {
    if (!host.current || !frame.candles.length) return;
    const key = `${data.symbol}:${timeframe}`;
    if (context.current !== key) visibleRange.current = null;
    context.current = key;
    const chart = createChart(host.current, {
      autoSize: true, height: 710,
      layout: { background: { type: ColorType.Solid, color: light ? '#ffffff' : COLORS.bg }, textColor: light ? '#526078' : '#8c9ab0', attributionLogo: true, fontFamily: '"JetBrains Mono", monospace', fontSize: 11 },
      grid: { vertLines: { color: light ? '#eef1f6' : COLORS.grid }, horzLines: { color: light ? '#eef1f6' : COLORS.grid } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { labelBackgroundColor: COLORS.blue }, horzLine: { labelBackgroundColor: COLORS.blue } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 5, borderColor: COLORS.border, tickMarkFormatter: (t: Time) => timeLabel(Number(t)) },
      localization: { timeFormatter: (t: Time) => timeLabel(Number(t)) },
      rightPriceScale: { borderColor: COLORS.border },
    });
    chartRef.current = chart;
    const price = chart.addSeries(CandlestickSeries, { upColor: COLORS.green, downColor: COLORS.red, wickUpColor: COLORS.green, wickDownColor: COLORS.red, borderVisible: false, priceFormat: { type: 'price', precision: 8, minMove: 0.00000001 } });
    price.setData(frame.candles.map(c => ({ ...c, time: c.time as Time, ...(c.closed ? {} : { color: '#6b7280', wickColor: '#6b7280' }) })));
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }, 1);
    volume.setData(frame.candles.map(c => ({ time: c.time as Time, value: c.volume, color: c.close >= c.open ? '#18c98b60' : '#ff526360' })));
    const seriesNames = Object.keys(frame.indicators).filter(name => name !== 'atr' && name !== 'volume_ma' && !disabled.includes(name));
    let pane = 2;
    const paneMap = new Map<string, number>();
    const colors = ['#f5aa42', '#5a86ff', '#bb91ff', '#2dd4bf', '#ec8ba8', '#92c964'];
    seriesNames.forEach((name, i) => {
      const overlay = name.startsWith('ema') || name.startsWith('bb_') || name === 'vwap' || name === 'psar';
      const family = name.startsWith('di_') || name.startsWith('adx') ? 'adx' : name.split('_')[0];
      if (!overlay && !paneMap.has(family)) paneMap.set(family, pane++);
      const line = chart.addSeries(LineSeries, { color: colors[i % colors.length], lineWidth: 1, title: name.toUpperCase(), priceLineVisible: false, lastValueVisible: false }, overlay ? 0 : paneMap.get(family)!);
      line.setData(frame.indicators[name].map(p => ({ ...p, time: p.time as Time })));
    });
    const volumeMa = chart.addSeries(LineSeries, { color: COLORS.blue, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'Volume MM' }, 1);
    if (frame.indicators.volume_ma) volumeMa.setData(frame.indicators.volume_ma.map(p => ({ ...p, time: p.time as Time })));
    const plan = data.analysis.plan;
    const areas = [...data.analysis.zones];
    if (plan) {
      const available_at = Date.parse(data.analysis.candle_close_at_utc ?? data.analysis.evaluated_at_utc)/1000;
      areas.push({ type: 'RISK', low: plan.stop, high: plan.entry, touches: 5, available_at },
        { type: 'REWARD', low: plan.entry, high: plan.tp2, touches: 5, available_at });
    }
    price.attachPrimitive(new ZonePrimitive(areas));
    const markers: SeriesMarker<Time>[] = frame.patterns.map(p => ({ time: p.time as Time, position: p.pattern.includes('BEAR') || p.pattern === 'SHOOTING_STAR' ? 'aboveBar' : 'belowBar', color: p.pattern.includes('BEAR') || p.pattern === 'SHOOTING_STAR' ? COLORS.red : COLORS.green, shape: 'circle', text: patternShort[p.pattern] ?? p.pattern }));
    markers.push(...frame.pivots.map(p => ({ time: p.time as Time, position: p.kind === 'H' ? 'aboveBar' as const : 'belowBar' as const, color: '#95a3b8', shape: 'circle' as const, text: p.label })));
    createSeriesMarkers(price, markers.sort((a,b) => Number(a.time)-Number(b.time)));
    for (const kind of ['H', 'L']) {
      const pivot = frame.pivots.filter(p => p.kind === kind).at(-1);
      if (pivot) price.createPriceLine({ price: pivot.price, color: '#8796ac66', lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: `Último ${pivot.label}` });
    }
    if (plan) for (const [title, value, color] of [['ENTRADA TEÓRICA', plan.entry, COLORS.blue], ['STOP SIMULADO', plan.stop, COLORS.red], ['TP1', plan.tp1, COLORS.green], ['TP2', plan.tp2, COLORS.green]] as const) {
      price.createPriceLine({ price: value, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
    }
    chart.panes()[0]?.setHeight(360);
    chart.panes()[1]?.setHeight(100);
    chart.panes().slice(2).forEach(p => p.setHeight(105));
    if (visibleRange.current) chart.timeScale().setVisibleRange(visibleRange.current);
    else chart.timeScale().fitContent();
    const describe = (t: number) => {
      const c = frame.candles.find(c => c.time === t);
      if (!c) return;
      const values = Object.entries(frame.indicators).flatMap(([name, points]) => {
        const point = points.find(p => p.time === t);
        return point ? [`${name.toUpperCase()} ${number(point.value, 4)}`] : [];
      });
      setLegend(`${timeLabel(t)} · ${c.closed ? 'fechado' : 'em formação'}   O ${number(c.open)}  H ${number(c.high)}  L ${number(c.low)}  C ${number(c.close)}  Vol ${number(c.volume, 1)}   ${number((c.close/c.open-1)*100, 2)}%`);
      const patterns = frame.patterns.filter(p => p.time === t).map(p => `${patternLabels[p.pattern]} · ${Object.entries(p.metrics).map(([k,v]) => `${k} ${number(v)}`).join(' · ')}`);
      setHoverDetail([...values, ...patterns].join(' | '));
    };
    chart.subscribeCrosshairMove(event => { if (event.time) describe(Number(event.time)); });
    describe(frame.candles[frame.candles.length-1].time);
    return () => {
      visibleRange.current = chart.timeScale().getVisibleRange();
      chart.remove();
      chartRef.current = null;
    };
  // Recreate on snapshots while preserving the user's visible time range.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, timeframe, light, preferences.timezone, disabled]);
  return <div className="mtf-chart-wrap">
    <div className="mtf-chart-heading"><strong>{data.symbol.replace('_', '/')} <span>{timeframe.toUpperCase()}</span></strong><button onClick={() => chartRef.current?.timeScale().scrollToRealTime()}>Último candle ↗</button></div>
    <div className="mtf-ohlc">{legend || 'Aguardando candles…'}</div>
    <div className="mtf-indicator-toggles">{Object.keys(frame.indicators).filter(n => n !== 'atr').map(name => <button key={name} className={disabled.includes(name) ? 'off' : ''} onClick={() => setDisabled(v => v.includes(name) ? v.filter(n => n !== name) : [...v, name])}>{name.toUpperCase()}</button>)}</div>
    {frame.error && <div className="mtf-warning">{frame.error}</div>}
    {frame.stale && <div className="mtf-warning">Dados atrasados — análise bloqueada quando os critérios de frescor falham.</div>}
    {!frame.candles.length && <div className="mtf-empty">Sem candles Gate disponíveis neste período.</div>}
    <div ref={host} style={{ width: '100%', height: frame.candles.length ? 710 : 0 }} />
    <div className="mtf-hover-detail" aria-live="polite">{hoverDetail}</div>
    <div className="mtf-chart-note">ENG Engolfo · MART Martelo · EC Estrela cadente · MARU Marubozu · HH/HL/LH/LL pivôs confirmados<br/>Faixas: suporte/resistência do 1H · Candle cinza: em formação, sem sinal · {preferences.timezone === 'UTC' ? 'UTC' : 'Brasília'}</div>
  </div>;
}
