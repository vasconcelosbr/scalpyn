"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { Expand, Minimize2 } from "lucide-react";
import { chartTickMarkFormatter, chartTimeFormatter } from "@/lib/datetime";

export type RadarCandle = {
  time: string;
  close_time: string;
  available_at: string | null;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  quality_status: string;
  contract_version: string;
};

export type RadarMarker = {
  kind: "START" | "PEAK" | "END" | "APPROVAL" | "ENTRY" | "EXIT";
  at: string;
  price?: number | null;
  pnl_pct?: number | null;
  link_id?: string;
};

const COLORS = {
  background: "#0a1422",
  grid: "rgba(116, 143, 181, .10)",
  border: "rgba(116, 143, 181, .24)",
  text: "#8091ac",
  green: "#52ddb1",
  red: "#f16478",
  blue: "#3187ff",
  amber: "#f4bd3f",
  purple: "#aa76ed",
  grey: "#a9b6ca",
};

function timestamp(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function containingTime(value: string, times: UTCTimestamp[]): UTCTimestamp | null {
  const target = timestamp(value) as number;
  const prior = times.filter((item) => (item as number) <= target);
  return prior.at(-1) ?? times[0] ?? null;
}

function movingAverage(rows: RadarCandle[], length: number) {
  return rows.flatMap((row, index) => {
    if (index < length - 1) return [];
    const slice = rows.slice(index - length + 1, index + 1);
    return [{ time: timestamp(row.time), value: slice.reduce((sum, item) => sum + item.close, 0) / length }];
  });
}

function markerStyle(marker: RadarMarker) {
  if (marker.kind === "APPROVAL") return { color: COLORS.purple, shape: "circle" as const, position: "aboveBar" as const, text: "Aprovado" };
  if (marker.kind === "ENTRY") return { color: COLORS.blue, shape: "arrowUp" as const, position: "belowBar" as const, text: "Entrada" };
  if (marker.kind === "EXIT") return { color: (marker.pnl_pct ?? 0) >= 0 ? COLORS.green : COLORS.red, shape: "arrowDown" as const, position: "aboveBar" as const, text: "Saída" };
  if (marker.kind === "START") return { color: COLORS.amber, shape: "arrowUp" as const, position: "belowBar" as const, text: "Início" };
  if (marker.kind === "PEAK") return { color: COLORS.amber, shape: "arrowDown" as const, position: "aboveBar" as const, text: "Pico" };
  return { color: COLORS.grey, shape: "square" as const, position: "aboveBar" as const, text: "Término" };
}

export function PumpRadarChart({
  candles,
  markers = [],
  title,
  subtitle,
  compact = false,
  showVolume = false,
  showMarkers = true,
  highlightStart,
  highlightEnd,
}: {
  candles: RadarCandle[];
  markers?: RadarMarker[];
  title: string;
  subtitle?: string;
  compact?: boolean;
  showVolume?: boolean;
  showMarkers?: boolean;
  highlightStart?: string;
  highlightEnd?: string | null;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const ordered = useMemo(() => [...candles].sort((a, b) => +new Date(a.time) - +new Date(b.time)), [candles]);

  const band = useMemo(() => {
    if (!highlightStart || !highlightEnd || ordered.length < 2) return null;
    const first = +new Date(ordered[0].time);
    const last = +new Date(ordered.at(-1)!.close_time);
    const span = Math.max(last - first, 1);
    const left = Math.max(0, Math.min(100, ((+new Date(highlightStart) - first) / span) * 100));
    const right = Math.max(left, Math.min(100, ((+new Date(highlightEnd) - first) / span) * 100));
    return { left: `${left}%`, width: `${right - left}%` };
  }, [highlightEnd, highlightStart, ordered]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || ordered.length === 0) return;
    const height = fullscreen ? Math.max(window.innerHeight - 120, 420) : compact ? 168 : 310;
    const chart = createChart(host, {
      width: host.clientWidth,
      height,
      layout: { background: { type: ColorType.Solid, color: COLORS.background }, textColor: COLORS.text, attributionLogo: false, fontFamily: '"JetBrains Mono", monospace' },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: COLORS.border, scaleMargins: { top: .08, bottom: showVolume ? .26 : .10 } },
      localization: { timeFormatter: chartTimeFormatter },
      timeScale: { borderColor: COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 2, barSpacing: compact ? 6 : 8, tickMarkFormatter: chartTickMarkFormatter },
      crosshair: { vertLine: { color: "rgba(49,135,255,.45)", labelBackgroundColor: COLORS.blue }, horzLine: { color: "rgba(49,135,255,.32)", labelBackgroundColor: COLORS.blue } },
      handleScroll: true,
      handleScale: true,
    });
    const candlesSeries = chart.addSeries(CandlestickSeries, { upColor: COLORS.green, downColor: COLORS.red, wickUpColor: COLORS.green, wickDownColor: COLORS.red, borderVisible: false, priceLineVisible: false });
    const data = ordered.map((row) => ({ time: timestamp(row.time), open: row.open, high: row.high, low: row.low, close: row.close }));
    candlesSeries.setData(data);
    const fast = chart.addSeries(LineSeries, { color: "#398cff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const slow = chart.addSeries(LineSeries, { color: "#d6a929", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    fast.setData(movingAverage(ordered, 9));
    slow.setData(movingAverage(ordered, 20));
    if (showVolume) {
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: .78, bottom: 0 } });
      volume.setData(ordered.map((row) => ({ time: timestamp(row.time), value: row.volume, color: row.close >= row.open ? "rgba(82,221,177,.38)" : "rgba(241,100,120,.38)" })));
    }
    if (showMarkers) {
      const times = data.map((row) => row.time);
      const seriesMarkers: SeriesMarker<UTCTimestamp>[] = markers.flatMap((marker, index) => {
        const time = containingTime(marker.at, times);
        if (time === null) return [];
        const style = markerStyle(marker);
        return [{ id: `${marker.kind}-${marker.link_id ?? index}`, time, size: marker.kind === "APPROVAL" ? 1.5 : 1.2, ...style }];
      }).sort((a, b) => (a.time as number) - (b.time as number));
      createSeriesMarkers(candlesSeries, seriesMarkers, { zOrder: "top" });
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(([entry]) => chart.applyOptions({ width: Math.floor(entry.contentRect.width) }));
    observer.observe(host);
    return () => { observer.disconnect(); chart.remove(); };
  }, [compact, fullscreen, markers, ordered, showMarkers, showVolume]);

  async function toggleFullscreen() {
    if (!cardRef.current) return;
    if (!document.fullscreenElement) await cardRef.current.requestFullscreen();
    else await document.exitFullscreen();
  }

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  return (
    <section ref={cardRef} className="relative overflow-hidden rounded-lg border border-[#22354f] bg-[#0a1422]">
      <header className="flex items-start justify-between gap-3 px-3 py-2">
        <div><div className="text-[12px] font-semibold text-[#c9d6e8]">{title}</div>{subtitle && <div className="mt-0.5 text-[10px] text-[#71839e]">{subtitle}</div>}</div>
        <button onClick={toggleFullscreen} className="rounded-md border border-[#263a57] p-1.5 text-[#8496b0] hover:border-[#398cff] hover:text-[#70a7ff]" aria-label={fullscreen ? "Sair da tela cheia" : "Ampliar gráfico"}>{fullscreen ? <Minimize2 size={14} /> : <Expand size={14} />}</button>
      </header>
      <div className="relative">
        {band && <div className="pointer-events-none absolute inset-y-0 z-[3] border-x border-[#4bc59a44] bg-[#3ac29418]" style={band} />}
        {ordered.length ? <div ref={hostRef} className="relative z-[2] w-full" /> : <div className="grid min-h-[168px] place-items-center px-4 text-center text-[11px] text-[#71839e]">Sem candles fechados disponíveis para esta janela.</div>}
      </div>
    </section>
  );
}
