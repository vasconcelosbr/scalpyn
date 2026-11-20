import type {Analysis,StructureContext} from '../components/mtf/types';

export const structureTooltip='Estrutura descreve pivôs já confirmados (passado). Não é previsão de direção.';
export const structureLabels:Record<string,string>={UP:'Alta',DOWN:'Baixa',RANGE:'Lateral'};
export function currentStructure(context:StructureContext|null|undefined, analysis:Analysis|undefined|null, now:number) {
  if(!context||!analysis||context.config_hash!==analysis.config_hash||context.evaluated_at!==analysis.evaluated_at_utc||
    (context.profile_id??null)!==(analysis.profile_id??null)||
    !Number.isFinite(Date.parse(context.valid_until))||now>=Date.parse(context.valid_until))return null;
  return context;
}
export function signed(value:number|null|undefined,digits=1) {
  return value==null||!Number.isFinite(value)?'—':`${value>0?'+':''}${value.toLocaleString('pt-BR',{maximumFractionDigits:digits})}`;
}
export function conflictText(context:StructureContext|null) {
  if(!context||context.conflict==null)return 'Conflito MTF: dados indisponíveis';
  return context.conflicts.map(c=>c.kind==='structure'
    ?`${c.timeframe.toUpperCase()}: ${structureLabels[c.state]??'—'} · ${c.sequence.slice(-4).join('·')}`
    :`Fluxo ${c.timeframe.toUpperCase()}: ${c.state==='outflow'?'Saída':'Entrada'} ${c.ratio==null?'—':signed(c.ratio*100,2)+'%'}`).join('\n');
}
