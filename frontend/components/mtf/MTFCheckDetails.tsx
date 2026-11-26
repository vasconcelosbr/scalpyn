import { diagnosticTitle, type DiagnosticContext, type DiagnosticInput, type DiagnosticState, type DiagnosticWindow, type RuleTrace, type TriggerCheck } from './types';

const states: Record<DiagnosticState,string> = {PASS:'Atendido',FAIL:'Não atendido',UNAVAILABLE:'Indisponível',WAITING:'Aguardando',DISABLED:'Desativado'};
const values: Record<string,string> = {UP:'Alta',DOWN:'Baixa',RANGE:'Lateral'};
const units: Record<string,string> = {seconds:'s',points:'pts',ratio:'×',ATR:'ATR','%':'%'};
export function diagnosticValue(value: TriggerCheck['actual'], unit: string): string {
  if (value == null || (typeof value === 'number' && !Number.isFinite(value))) return 'Não disponível';
  if (typeof value === 'boolean') return value ? 'Sim' : 'Não';
  if (typeof value === 'string') return values[value] ?? value;
  return `${value.toLocaleString('pt-BR', {maximumSignificantDigits:10})}${units[unit] ? ` ${units[unit]}` : ''}`;
}
const summaries: Record<string,string> = {
  btc_downtrend:'Classifica a estrutura do Bitcoin pelos topos e fundos confirmados e aplica o veto de baixa.',
  'rules:GATILHO':'Combina as condições configuradas para confirmar a entrada.',
  'rules:ARMADO':'Combina as condições configuradas para armar a entrada.',
  'audit:structure_integrity':'Verifica se a estrutura principal permanece válida após os fechamentos.',
  'audit:zone_integrity':'Verifica se a estrutura do período da zona permanece válida após os fechamentos.',
  'audit:structure_alignment':'Verifica a direção dos topos e fundos confirmados da estrutura principal.',
  'audit:zone_alignment':'Verifica se a estrutura do período da zona forma a direção de alta exigida.',
};
export const checkSummary = (row: TriggerCheck) => row.explanation?.description ?? summaries[row.id]
  ?? 'Compara o valor observado com a condição exigida nesta avaliação.';

const parameterNames: Record<string,string> = {
  pivot_left:'Candles à esquerda do pivô',pivot_right:'Candles à direita para confirmar o pivô',
  candle_limit:'Limite de candles solicitado',btc_trend_timeframe:'Período do Bitcoin',
  period:'Período',periods:'Períodos',fast:'Período rápido',slow:'Período lento',signal:'Período do sinal',
  lookback:'Janela de referência',deviation:'Desvios',k:'Período K',d:'Período D',smooth:'Suavização',
  af_start:'Aceleração inicial',af_increment:'Incremento da aceleração',af_max:'Aceleração máxima',
  sample_window:'Janela de cálculo (candles)',timeframe:'Timeframe',enabled:'Ativado',
  vol_lookback:'Candles anteriores para média de volume',vol_mult:'Multiplicador de volume',
  zone_proximity_atr:'Proximidade máxima em ATR',zone_atr_mult:'Largura da zona em ATR',
  zone_min_touches:'Toques mínimos da zona',zone_lookback_candles:'Janela de formação das zonas',
  rejection_lookback:'Janela de rejeição',trigger_patterns:'Padrões de gatilho',
  force_patterns:'Padrões de força',rejection_patterns:'Padrões de rejeição',
  min_range_atr:'Amplitude mínima em ATR',doji_body_pct:'Fração máxima do corpo de doji',
  pin_wick_ratio:'Multiplicador do corpo para o pavio',pin_wick_pct:'Fração mínima do pavio',
  pin_close_fraction:'Fração de proximidade do fechamento',engulf_include_wicks:'Engolfo inclui pavios',
  marubozu_wick_pct:'Fração máxima dos pavios',max_spread_pct:'Spread máximo (%)',
  stop_buffer_atr:'Margem do stop em ATR',stop_sizing:'Dimensionamento do stop',
  max_stop_atr:'Distância máxima do stop em ATR',min_rr_net:'Retorno/risco líquido mínimo',
  rr_bonus_threshold:'Retorno/risco para bônus',fee_per_side_pct:'Taxa por lado (%)',
  slippage_pct:'Slippage (%)',min_score:'Score mínimo',score_weights:'Pesos do score',
  stale_tf_multiplier:'Validade em múltiplos do timeframe',
  'veto_filters.btc_downtrend':'Veto de baixa do Bitcoin',
  'context_audit.require_structure_alignment':'Exige alta na estrutura principal',
  'context_audit.require_zone_alignment':'Exige alta no período da zona',
  'context_audit.require_structure_integrity':'Exige estrutura íntegra',
  'context_audit.require_trigger_lh_break':'Exige rompimento do topo descendente',
  'context_audit.min_stop_atr_trigger':'Distância mínima do stop em ATR',
  'context_audit.require_stop_beyond_trigger_swing':'Exige stop abaixo do fundo operacional',
  'context_audit.require_stop_beyond_arming_zone':'Exige stop abaixo do suporte',
  'context_audit.min_reference_rr_gross':'Retorno/risco bruto mínimo do alvo',
  'context_audit.require_positive_reference_reward_net':'Exige retorno líquido positivo',
  'stop_sizing.target_pct':'Alvo de referência (%)',pump_flow:'Parâmetros do fluxo',
};
const show = (value: unknown): string => {
  if (Array.isArray(value)) return value.map(show).join(' · ');
  if (value && typeof value === 'object') return Object.entries(value).map(([key,v])=>`${parameterNames[key] ?? key}: ${show(v)}`).join('; ');
  return diagnosticValue(value as TriggerCheck['actual'],'');
};
const operators: Record<string,string> = {'=':'igual a','!=':'diferente de','>':'maior que','>=':'maior ou igual a','<':'menor que','<=':'menor ou igual a',between:'entre os limites (inclusive)',crosses_above:'cruza para cima',crosses_below:'cruza para baixo'};
export const conditionText = (row: TriggerCheck) => `${operators[row.operator] ?? row.operator} ${Array.isArray(row.expected) ? row.expected.map(v=>diagnosticValue(v,row.unit)).join(' e ') : diagnosticValue(row.expected,row.unit)}`;
type DateLabel = (value?:string|null)=>string;
function stamp(value: number|string|null|undefined, date:DateLabel) {
  if(value==null) return 'Não disponível';
  return date(typeof value==='number' ? new Date(value*1000).toISOString() : value);
}
function Parameters({parameters}:{parameters?:Record<string,unknown>}) {
  return parameters && Object.keys(parameters).length ? <dl className="mtf-technical-values">
    {Object.entries(parameters).map(([key,value])=><div key={key}><dt>{parameterNames[key] ?? key}</dt><dd>{show(value)}</dd></div>)}
  </dl> : <p className="mtf-muted">Parâmetros adicionais não registrados nesta avaliação.</p>;
}
function Window({window,date}:{window?:DiagnosticWindow|null;date:DateLabel}) {
  return <dl className="mtf-technical-values">
    <div><dt>Tipo de janela</dt><dd>{window?.scope==='indicator_sample'?'Amostra utilizada pelo indicador':'Histórico disponível à avaliação'}</dd></div>
    <div><dt>Início da janela</dt><dd>{stamp(window?.start_at,date)}</dd></div>
    <div><dt>Fim da janela</dt><dd>{stamp(window?.end_at,date)}</dd></div>
    <div><dt>Candles nesta janela</dt><dd>{show(window?.candle_count)}</dd></div>
  </dl>;
}
function Input({input,label,date}:{input:DiagnosticInput;label:string;date:DateLabel}) {
  const cfg=input.configuration;
  return <section className="mtf-technical-input" aria-label={label}>
    <h5>{label}</h5>
    {!input.ref ? <p>Constante configurada: {show(input.constant)}</p> : <>
      <dl className="mtf-technical-values">
        <div><dt>Indicador ou fonte</dt><dd>{cfg ? cfg.family.toUpperCase() : input.ref}</dd></div>
        {cfg && <div><dt>Saída utilizada</dt><dd>{cfg.output}</dd></div>}
        <div><dt>Timeframe da fonte</dt><dd>{input.timeframe?.toUpperCase() ?? 'Não disponível'}</dd></div>
        {cfg && <div><dt>Janela de cálculo (candles)</dt><dd>{show(cfg.sample_window)}</dd></div>}
        <div><dt>Fechamento da amostra</dt><dd>{stamp(input.close_at,date)}</dd></div>
        {input.valid_until != null && <div><dt>Amostra válida até</dt><dd>{stamp(input.valid_until,date)}</dd></div>}
      </dl>
      {input.method && <p>{input.method}</p>}
      <Parameters parameters={cfg?.parameters ?? input.parameters}/>
      <Window window={input.window} date={date}/>
    </>}
  </section>;
}
function RuleTree({tree,checks,date,context}:{tree:RuleTrace;checks:TriggerCheck[];date:DateLabel;context?:DiagnosticContext}) {
  if(tree.check_id) {
    const check=checks.find(c=>c.id===tree.check_id);
    if(!check) return <p className="mtf-muted">Detalhe desta condição indisponível na avaliação.</p>;
    return <div className="mtf-rule-leaf">
      <strong>{diagnosticTitle(check)}</strong> <span className={`mtf-check-state ${tree.state}`}>{states[tree.state]}</span>
      <p>Observado: {diagnosticValue(check.actual,check.unit)} · Exigido: {conditionText(check)}</p>
      <CheckDetails row={check} checks={checks} date={date} context={context}/>
    </div>;
  }
  return <section className="mtf-rule-group">
    <p><b>{tree.logic==='AND'?'Grupo E · todas as condições ativas':'Grupo OU · alguma condição ativa'}</b> <span className={`mtf-check-state ${tree.state}`}>{states[tree.state]}</span></p>
    {tree.children?.map((child,i)=><RuleTree key={child.check_id ?? i} tree={child} checks={checks} date={date} context={context}/>)}
  </section>;
}
export function CheckDetails({row,checks,date,context}:{row:TriggerCheck;checks:TriggerCheck[];date:DateLabel;context?:DiagnosticContext}) {
  const detail=row.explanation, evidence=detail?.evidence;
  return <details className="mtf-check-details">
    <summary>Como é calculado e por que este resultado</summary>
    {detail ? <>
      <p>{detail.method}</p>
      <dl className="mtf-technical-values">
        {detail.operational_timeframe && <div><dt>Timeframe operacional do estágio</dt><dd>{detail.operational_timeframe.toUpperCase()}</dd></div>}
        <div><dt>Timeframes analisados</dt><dd>{detail.timeframes.map(tf=>tf.toUpperCase()).join(' · ') || 'Não disponível / sem série temporal própria'}</dd></div>
        {detail.domain && <div><dt>Valores possíveis</dt><dd>{detail.domain}</dd></div>}
        <div><dt>Condição de aprovação</dt><dd>{conditionText(row)}</dd></div>
        <div><dt>Estado nesta avaliação</dt><dd>{states[row.state]}</dd></div>
      </dl>
      {['crosses_above','crosses_below'].includes(row.operator) && <p>O cruzamento compara a relação anterior e a atual; igualdade atual não confirma o cruzamento.</p>}
      <Parameters parameters={detail.parameters}/>
      {detail.window && <Window window={detail.window} date={date}/>}
      {!detail.window && !detail.inputs && !row.rule_tree && <p className="mtf-muted">Janela efetiva não registrada neste cartão.</p>}
      {detail.inputs && <>
        <Input input={detail.inputs.left} label="Fonte do valor observado" date={date}/>
        <Input input={detail.inputs.right} label="Fonte da exigência" date={date}/>
      </>}
      {row.previous && <div className="mtf-technical-input"><h5>Comparação anterior do cruzamento</h5>
        <p>{stamp(row.previous.close_at,date)} · Observado: {show(row.previous.actual)} · Referência: {show(row.previous.expected)}</p>
        <p>Fechamento da fonte observada: {stamp(row.previous.left_sample?.close_at,date)} · Fechamento da referência: {stamp(row.previous.right_sample?.close_at,date)}</p>
      </div>}
      {evidence?.pivots && <div className="mtf-evidence-table"><table>
        <caption>Pivôs usados na comparação</caption>
        <thead><tr><th>Pivô</th><th>Preço</th><th>Candle</th><th>Confirmado em</th></tr></thead>
        <tbody>{evidence.pivots.map(p=><tr key={`${p.kind}:${p.time}`}><td>{p.label}</td><td>{show(p.price)}</td><td>{stamp(p.time,date)}</td><td>{stamp(p.confirmed_at,date)}</td></tr>)}</tbody>
      </table>{!evidence.pivots.length && <p>Não disponível nesta avaliação.</p>}</div>}
      {evidence?.complete===false && <p>Os pivôs disponíveis não formam uma estrutura completa. A classificação exibida conserva a regra desta avaliação.</p>}
      {evidence?.last_break && <div className="mtf-technical-input">
        <h5>{evidence.broken_at != null ? 'Rompimento ainda ativo' : 'Último rompimento histórico'}</h5>
        {evidence.broken_at != null && <p>Início do rompimento ativo: {stamp(evidence.broken_at,date)}.</p>}
        <p>Último evento: {stamp(evidence.last_break.at,date)}. Fechamento {show(evidence.last_break.close)}, nível {evidence.last_break.pivot.label} em {show(evidence.last_break.pivot.price)}.</p>
        <p>{evidence.intact===false ? 'A estrutura ainda não recuperou a integridade nesta avaliação.' : evidence.intact===true ? 'A integridade foi recuperada; este evento é histórico.' : 'Estado de recuperação indisponível.'}</p>
      </div>}
      {evidence?.reference_candle && <p>Candle de referência: {stamp(evidence.reference_candle.close_at,date)} · Fechamento {show(evidence.reference_candle.close)}.</p>}
      {evidence?.reason && <p>Motivo registrado pela fonte: {evidence.reason}.</p>}
      {row.rule_tree && <><p>O resultado obrigatório é o do grupo. Uma alternativa falsa não constitui veto independente. Dados ausentes em condições ativas mantêm o grupo indisponível.</p><RuleTree tree={row.rule_tree} checks={checks} date={date} context={context}/></>}
      <p className="mtf-muted">Fonte: {detail.source_symbol ?? context?.symbol ?? 'Não disponível'} · {detail.source ?? context?.source ?? 'Não disponível'} · Corte: {stamp(context?.cutoff,date)} · {context?.mode==='PREVIEW'?'Prévia em formação, sem confirmação':'Avaliação de candles fechados'}.</p>
      <details className="mtf-technical-json"><summary>Parâmetros e evidências originais</summary><pre>{JSON.stringify({check:row.id,observed:row.actual,operator:row.operator,expected:row.expected,state:row.state,explanation:detail,previous:row.previous,rule_tree:row.rule_tree,context},null,2)}</pre></details>
    </> : <p className="mtf-muted">Detalhes técnicos não registrados nesta avaliação. Parâmetros, janela e evidências: NÃO DISPONÍVEL. A configuração atual não é usada para preencher o histórico.</p>}
  </details>;
}
