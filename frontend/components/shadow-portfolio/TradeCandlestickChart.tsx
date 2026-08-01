"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ShadowTradeChartResponse } from "./types";

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
  return new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
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
        attributionLogo: false,
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
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 3,
        barSpacing: 9,
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
      const isTp = data.outcome === "TP_HIT";
      markers.push({
        id: "exit",
        time: exitCandle,
        position: "atPriceTop",
        price: data.exit_price,
        shape: "arrowDown",
        color: isTp ? COLORS.green : COLORS.red,
        text: `S · ${isTp ? "TP" : "SL"} ${exactTime(data.exit_timestamp)}`,
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
        color: data.outcome === "TP_HIT" ? COLORS.green : COLORS.red,
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
