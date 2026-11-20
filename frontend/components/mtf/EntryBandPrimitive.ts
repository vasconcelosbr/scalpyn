import type { ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, Time } from 'lightweight-charts';

/** Current price guide, like an entry price line; never a historical signal. */
export class EntryBandPrimitive implements ISeriesPrimitive<Time> {
  private series?: ISeriesApi<'Candlestick'>;
  constructor(private low: number, private high: number, private color: string) {}
  attached({series, requestUpdate}: SeriesAttachedParameter<Time>) { this.series=series as ISeriesApi<'Candlestick'>; requestUpdate(); }
  detached() { this.series=undefined; }
  updateAllViews() {}
  paneViews() {
    return [{zOrder:()=> 'bottom' as const,renderer:()=>({draw:(target:{useMediaCoordinateSpace:(draw:(scope:{context:CanvasRenderingContext2D;mediaSize:{width:number;height:number}})=>void)=>void})=>{
      if (!this.series) return;
      const top=this.series.priceToCoordinate(this.high),bottom=this.series.priceToCoordinate(this.low);
      if(top==null||bottom==null)return;
      target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
        ctx.save();
        ctx.fillStyle=this.color;ctx.globalAlpha=.12;
        ctx.fillRect(0,Math.max(0,top),mediaSize.width,Math.max(0,Math.min(mediaSize.height,bottom)-Math.max(0,top)));
        ctx.globalAlpha=.85;ctx.strokeStyle=this.color;ctx.setLineDash([6,4]);ctx.lineWidth=1;
        for(const y of [top,bottom])if(y>=0&&y<=mediaSize.height){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(mediaSize.width,y);ctx.stroke();}
        ctx.restore();
      });
    }})}];
  }
}
