import type { IChartApi, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, Time } from 'lightweight-charts';
import type { Zone } from './types';

interface CanvasTarget {
  useMediaCoordinateSpace(callback: (scope: { context: CanvasRenderingContext2D; mediaSize: { width: number; height: number } }) => void): void;
}

/** Chart-native rectangles stay anchored to price/time while panning/zooming. */
export class ZonePrimitive implements ISeriesPrimitive<Time> {
  private chart?: IChartApi;
  private series?: ISeriesApi<'Candlestick'>;
  private update?: () => void;
  constructor(private zones: Zone[]) {}
  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>) {
    this.chart = chart;
    this.series = series as ISeriesApi<'Candlestick'>;
    this.update = requestUpdate;
    requestUpdate();
  }
  detached() { this.chart = undefined; this.series = undefined; this.update = undefined; }
  updateAllViews() {}
  paneViews() {
    return [{ zOrder: () => 'bottom' as const, renderer: () => ({ draw: (target: CanvasTarget) => {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        for (const zone of this.zones) {
          const top = this.series!.priceToCoordinate(zone.high);
          const bottom = this.series!.priceToCoordinate(zone.low);
          if (top == null || bottom == null) continue;
          const position = zone.type === 'RISK' || zone.type === 'REWARD';
          const start = this.chart!.timeScale().timeToCoordinate(zone.available_at as Time) ?? (position ? mediaSize.width-60 : 0);
          const alpha = Math.min(0.28, 0.06 + zone.touches * 0.025);
          const positive = zone.type === 'SUPPORT' || zone.type === 'REWARD';
          ctx.fillStyle = positive ? `rgba(45,212,160,${alpha})` : `rgba(251,113,133,${alpha})`;
          ctx.fillRect(start, top, mediaSize.width - start, Math.max(2, bottom - top));
          ctx.fillStyle = positive ? '#2dd4a0' : '#fb7185';
          ctx.font = '10px monospace';
          if (!position) ctx.fillText(`${zone.type === 'SUPPORT' ? 'SUPORTE' : 'RESISTÊNCIA'} · ${zone.touches} toques`, Math.max(4, start + 6), top - 4);
        }
      });
    } }) }];
  }
}
