type Check = {id:string;label:string;role:string;state:string;actual:unknown;expected:unknown;note?:string};
type Analysis = {status?:string;mode?:string;operational_effect?:boolean;explanation?:string[];
  btc_context?:{exempt?:boolean};trigger_diagnostics?:{checks?:Check[]}};
type Frame = {atr?:number;last_closed_candle?:{close?:number}};
const value=(v:unknown)=>v==null?'Não disponível':typeof v==='object'?JSON.stringify(v):String(v);

export function MTFDecisionEvidence({payload,replay}:{payload:Record<string,unknown>;replay?:{status?:string;reason?:string;scope?:string}}){
  const analysis=(payload.analysis??{}) as Analysis;
  const frames=(payload.frames??{}) as Record<string,Frame>;
  return <section aria-label="Evidências da decisão">
    <h3>Por que este estado foi registrado</h3>
    <p>{analysis.status} · {analysis.mode} · {analysis.operational_effect===false?'Sem execução de ordem real por este motor':'Efeito operacional não informado'}</p>
    {analysis.explanation?.map((line,index)=><p key={index}>{line}</p>)}
    {analysis.btc_context?.exempt&&<p>Veto de contexto BTC: não aplicável ao próprio BTC nesta configuração histórica. O veto de tendência do ativo é independente.</p>}
    <p>Reprodução da decisão: {replay?.status==='MATCH'?'Conferida com entradas congeladas':replay?.status==='NOT_REPRODUCIBLE'?'Não reproduzível integralmente: esta captura antiga não congelou todas as entradas':replay?.status??'Não disponível'}. O replay da decisão não equivale a recalcular todos os indicadores.</p>
    <div className="history-table"><table><thead><tr><th>Condição</th><th>Papel efetivo</th><th>Resultado</th><th>Observado</th><th>Exigido</th><th>Explicação</th></tr></thead><tbody>
      {analysis.trigger_diagnostics?.checks?.map((check,index)=><tr key={`${check.id}-${index}`}><td>{check.label}</td><td>{({required:'Obrigatória',veto:'Veto',score:'Pontuação',context:'Contexto'} as Record<string,string>)[check.role]??check.role}</td><td>{check.state}</td><td>{value(check.actual)}</td><td>{value(check.expected)}</td><td>{check.note??'—'}</td></tr>)}
    </tbody></table></div>
    <h3>Entradas do motor capturadas</h3>
    <div className="history-table"><table><thead><tr><th>Timeframe</th><th>Fechamento-base MTF</th><th>ATR do motor</th></tr></thead><tbody>{Object.entries(frames).map(([tf,frame])=><tr key={tf}><td>{tf}</td><td>{value(frame.last_closed_candle?.close)}</td><td>{value(frame.atr)}</td></tr>)}</tbody></table></div>
    <p>Indicadores de contexto vêm de outros produtores e podem ter candles e parâmetros diferentes. “Disponível” não significa usado numa regra. O horário do extremo histórico não é o horário de atualização. MACD “positive” indica posição acima da linha de sinal; não indica aceleração.</p>
  </section>;
}
