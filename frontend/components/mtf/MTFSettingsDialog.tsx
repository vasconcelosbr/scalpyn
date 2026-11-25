'use client';

import { MTFJsonTools } from './MTFJsonTools';
import { useEffect, useRef, useState } from 'react';
import { X, SlidersHorizontal, Crosshair, Layers, Settings2, ShieldCheck } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { configValue, schemaAt, settingsDraft, settingsForSave, validateSettings, withConfigValue } from '@/lib/mtfSettings';
import { patternLabels, type Bootstrap, type MTFConfig } from './types';

type Tab = 'flow' | 'armed' | 'trigger' | 'vetoes' | 'shared' | 'advanced';
type FieldSpec = {path: string; label: string; hint: string; unit?: string; kind?: 'patterns' | 'boolean' | 'btc-timeframe'};
const field = (path: string, label: string, hint: string, unit?: string): FieldSpec => ({path, label, hint, unit});
const sections: {tab: Tab; title: string; note?: string; fields: FieldSpec[]}[] = [
  {tab:'vetoes',title:'Auditoria obrigatória de contexto e risco',note:'Bloqueios independentes do score, confirmados somente no fechamento. Não há exceção de contratendência. O stop escolhido é validado, nunca alargado automaticamente. Alterações valem para novas avaliações.',fields:[
    {path:'context_audit.enabled',label:'Ativar auditoria obrigatória',hint:'Sinais reprovados deixam de ser publicados como Gatilho.',kind:'boolean'},
    {path:'context_audit.require_structure_alignment',label:'Exigir direção superior alinhada',hint:'Exige estrutura altista para LONG.',kind:'boolean'},
    {path:'context_audit.require_zone_alignment',label:'Exigir direção tática alinhada',hint:'Suporte próximo não substitui tendência alinhada.',kind:'boolean'},
    {path:'context_audit.require_structure_integrity',label:'Exigir estrutura íntegra',hint:'Fechamento além do pivô invalida a estrutura até nova formação confirmada.',kind:'boolean'},
    {path:'context_audit.require_trigger_lh_break',label:'Exigir rompimento do LH operacional',hint:'Um engolfo deve fechar acima do último topo descendente, quando houver.',kind:'boolean'},
    {path:'context_audit.require_stop_beyond_trigger_swing',label:'Stop além do fundo operacional',hint:'Exige stop abaixo do último fundo confirmado.',kind:'boolean'},
    {path:'context_audit.require_stop_beyond_arming_zone',label:'Stop além da zona de suporte',hint:'Exige stop abaixo da borda inferior da zona que armou o sinal.',kind:'boolean'},
    field('context_audit.min_stop_atr_trigger','Distância mínima do stop','Limiar experimental, sem garantia de resultado.','ATR operacional'),
    field('context_audit.min_reference_rr_gross','RR bruto mínimo do alvo percentual','Usa o alvo de referência configurado em Stop pelo RR bruto.','R:R'),
    {path:'context_audit.require_positive_reference_reward_net',label:'Exigir retorno líquido positivo',hint:'Desconta taxas e slippage do alvo percentual de referência.',kind:'boolean'},
  ]},
  {tab:'flow',title:'Capital e fluxo · comparação Shadow',note:'Hipótese observacional. Não altera score, vetos, plano ou admissões. A comparação usa os mesmos resultados Shadow, separados pela confirmação registrada na entrada.',fields:[
    {path:'pump_flow.enabled',label:'Ativar contexto e comparação',hint:'Registra os dados nas próximas avaliações. Não reclassifica o histórico.',kind:'boolean'},
    field('pump_flow.min_coverage','Cobertura mínima','Fração mínima de minutos e símbolos disponíveis. Ausência não equivale a fluxo neutro.','fração'),
    field('pump_flow.max_source_age_seconds','Idade máxima do snapshot Pump','Validade do universo monitorado consultado.','segundos'),
    field('pump_flow.max_evaluation_delay_seconds','Atraso máximo da avaliação','Tempo máximo entre fechamento do candle e captura do contexto.','segundos'),
    field('pump_flow.query_timeout_ms','Tempo máximo da consulta','Se a consulta falhar, mantém a avaliação MTF e marca fluxo indisponível.','ms'),
  ]},
  {tab:'flow',title:'Capital do universo',note:'Janelas móveis de minutos fechados, terminando no fechamento MTF. Cada janela tem seu próprio limite. Janelas sobrepostas não somam pontos.',fields:[
    ...(['4h','1h','5m'] as const).flatMap(tf=>[
      field(`pump_flow.capital_neutral_ratio.${tf}`,`Faixa neutra · ${tf.toUpperCase()}`,'Razão líquida entre compra e venda. Acima do limite: entrada; abaixo do negativo: saída.','fração'),
      {path:`pump_flow.require_capital.${tf}`,label:`Exigir contexto · ${tf.toUpperCase()}`,hint:'Para a comparação confirmada, exige Capital neutro ou de entrada com dados suficientes.',kind:'boolean' as const},
    ]),
  ]},
  {tab:'flow',title:'Confirmação por ativo · 5m',fields:[
    field('pump_flow.asset_min_delta','Delta normalizado mínimo','(Compra − venda) / (compra + venda), em USDT.','fração'),
    field('pump_flow.asset_min_buy_persistence','Persistência compradora mínima','Fração dos minutos disponíveis com compra maior que venda. Exige também o delta mínimo.','fração'),
  ]},
  {tab:'armed', title:'Estrutura e aproximação', note:'ARMADO exige proximidade da zona e ausência dos vetos habilitados. Os filtros de direção ficam em Vetos. Força e rejeição contribuem para o score.', fields:[
    field('pivot_left','Pivô · candles à esquerda','Confirma a estrutura de tendência. Compartilhado entre os timeframes.','candles'),
    field('pivot_right','Pivô · candles à direita','Só confirma o pivô após os candles seguintes. Compartilhado.','candles'),
    field('zone_atr_mult','Largura e agrupamento da zona','Distância usada para agrupar pivôs e formar a faixa de suporte/resistência.','× ATR'),
    field('zone_min_touches','Toques mínimos','Quantidade mínima de pivôs confirmados na zona.','toques'),
    field('zone_proximity_atr','Proximidade para armar','Distância máxima entre o preço e a zona de suporte.','× ATR'),
    field('rejection_lookback','Janela de rejeição','Busca rejeição nos candles recentes que intersectam a zona.','candles'),
  ]},
  {tab:'armed', title:'Padrões e contribuição para o score', fields:[
    {path:'force_patterns',label:'Padrões de força',hint:'Reconhecidos no contexto de direção.',kind:'patterns'},
    {path:'rejection_patterns',label:'Padrões de rejeição',hint:'Reconhecidos na região de suporte.',kind:'patterns'},
    field('score_weights.trend','Peso · tendência','Pontos por estrutura de alta.','pontos'),
    field('score_weights.force','Peso · força','Pontos por padrão de força com direção de alta.','pontos'),
    field('score_weights.zone','Peso · zona','Pontos por proximidade da zona.','pontos'),
    field('score_weights.rejection','Peso · rejeição','Pontos por rejeição próxima da zona.','pontos'),
  ]},
  {tab:'trigger', title:'Confirmação do gatilho', note:'GATILHO exige proximidade, padrão no último candle fechado, plano válido e score mínimo, sem vetos. Volume contribui para o score conforme seu peso.', fields:[
    {path:'trigger_patterns',label:'Padrões aceitos no gatilho',hint:'A seleção preserva a prioridade dos padrões existentes.',kind:'patterns'},
    field('vol_lookback','Média de volume','Compara o candle do gatilho com os candles anteriores.','candles'),
    field('vol_mult','Volume relativo mínimo','Volume necessário para receber os pontos de volume.','× média'),
    field('min_score','Score mínimo','Soma necessária para confirmar o gatilho.','pontos'),
    field('score_weights.trigger','Peso · padrão de gatilho','Pontos pelo padrão de entrada.','pontos'),
    field('score_weights.volume','Peso · volume','Pontos pela confirmação de volume.','pontos'),
    field('score_weights.rr','Peso · retorno/risco','Pontos pelo bônus de retorno/risco.','pontos'),
  ]},
  {tab:'trigger', title:'Validade do plano', fields:[
    field('stop_buffer_atr','Margem do stop','Margem sob a mínima do candle; o spread também participa do cálculo.','× ATR'),
    field('rr_bonus_threshold','Retorno/risco para bônus','Relação necessária para receber os pontos de retorno/risco.','R:R'),
  ]},
  {tab:'trigger', title:'Stop pelo RR bruto', note:'Calcula a distância do stop a partir do preço de confirmação: alvo percentual ÷ RR bruto. Substitui a mínima do candle e a margem ATR no cálculo do stop. Não é um veto. Vale para novos planos; os stops já registrados permanecem congelados. Os alvos estruturais do Shadow não são alterados.', fields:[
    {path:'stop_sizing.enabled',label:'Calcular stop pelo RR bruto',hint:'Sim usa o alvo percentual e o RR abaixo. Não mantém o stop pela mínima do candle, ATR e spread.',kind:'boolean'},
    field('stop_sizing.target_pct','Alvo de referência para o stop','Percentual de alta usado apenas para dimensionar o stop. Mantenha igual ao alvo do replay se desejar o mesmo RR nesse acompanhamento.','%'),
    field('stop_sizing.gross_rr','RR bruto para calcular o stop','Retorno dividido pelo risco, antes dos custos. Alvo de 1% com RR 2 gera distância de stop de 0,5%.','R:R'),
  ]},
  {tab:'vetoes', title:'Filtros de estratégia', note:'Sim ativa o veto; Não ignora somente esse filtro. Os limites são preservados ao desativar. As alterações só entram em vigor ao salvar e afetam as próximas avaliações MTF.', fields:[
    {path:'veto_filters.asset_downtrend',label:'Vetar tendência de baixa do ativo',hint:'Impede ARMADO e GATILHO quando a estrutura do ativo está em baixa. A direção continua sendo calculada e exibida.',kind:'boolean'},
    {path:'veto_filters.asset_range',label:'Vetar tendência lateral do ativo',hint:'Impede ARMADO e GATILHO quando a estrutura do ativo está lateral. Não altera os pontos de tendência.',kind:'boolean'},
    {path:'veto_filters.btc_downtrend',label:'Vetar Bitcoin em tendência de baixa',hint:'Filtra os demais ativos pelo contexto do Bitcoin. Desativado, o contexto do Bitcoin não é exigido; os dados do próprio ativo continuam obrigatórios.',kind:'boolean'},
    {path:'btc_trend_timeframe',label:'Timeframe da tendência do Bitcoin',hint:'Usa pivôs confirmados de candles fechados. Salvar outra opção inicia uma nova versão de configuração; o histórico é preservado.',kind:'btc-timeframe'},
    {path:'veto_filters.spread',label:'Vetar spread acima do limite',hint:'Impede ARMADO e GATILHO quando o spread supera o limite. Desativar não dispensa a leitura do spread nem remove custos do plano.',kind:'boolean'},
    field('max_spread_pct','Spread máximo','Limite aplicado quando o filtro de spread está ativo.','%'),
    {path:'veto_filters.stop_distance',label:'Vetar stop distante demais',hint:'Rejeita o plano quando a distância até o stop supera o limite. O stop deve continuar válido mesmo com o filtro desativado.',kind:'boolean'},
    field('max_stop_atr','Distância máxima do stop','Limite aplicado quando o filtro de distância do stop está ativo.','× ATR'),
    {path:'veto_filters.risk_reward',label:'Vetar retorno/risco abaixo do mínimo',hint:'Aplica o mínimo ao plano e à entrada simulada. Desativar não remove taxas, slippage, cálculo de risco ou o bônus de score.',kind:'boolean'},
    field('min_rr_net','Retorno/risco líquido mínimo','Limite aplicado quando o filtro de retorno/risco está ativo.','R:R'),
  ]},
  {tab:'vetoes', title:'Validação de dados e do plano', note:'Candles e ATR ausentes, spread indisponível, dados vencidos, alvos estruturais ausentes e plano inválido continuam impedindo a avaliação. Não são filtros opcionais de estratégia. O contexto do Bitcoin só é obrigatório quando seu filtro está ativo.', fields:[
    field('stale_tf_multiplier','Validade dos dados','Tolerância para dados antigos. Dados vencidos impedem ARMADO e GATILHO.','× timeframe'),
  ]},
  {tab:'shared', title:'Indicador e geometria dos candles', note:'Estes parâmetros são usados nas duas etapas. A edição vale somente no MTF. EMAs, RSI e ADX exibidos no gráfico não são condições desses estados.', fields:[
    field('atr_period','Período do ATR','Base de volatilidade para zonas, padrões e stops. Ao salvar, fica definido no MTF.','candles'),
    field('min_range_atr','Amplitude mínima do candle','Amplitude mínima para reconhecer um padrão.','× ATR'),
    field('doji_body_pct','Corpo máximo do Doji','Fração da amplitude total do candle.','fração'),
    field('pin_wick_ratio','Pin · pavio/corpo','Relação mínima entre o pavio dominante e o corpo.','razão'),
    field('pin_wick_pct','Pin · pavio/amplitude','Fração mínima ocupada pelo pavio dominante.','fração'),
    field('pin_close_fraction','Pin · posição do fechamento','Distância máxima do fechamento à extremidade favorável.','fração'),
    {path:'engulf_include_wicks',label:'Engolfo inclui pavios',hint:'Compara toda a amplitude; desativado compara os corpos.',kind:'boolean'},
    field('marubozu_wick_pct','Marubozu · pavios máximos','Fração máxima da amplitude ocupada pelos dois pavios.','fração'),
  ]},
  {tab:'shared', title:'Histórico e validade', fields:[
    ...(['4h','1h','5m'] as const).map(tf=>field(`candle_limits.${tf}`,`Histórico ${tf.toUpperCase()}`,'Janela de candles usada na avaliação.','candles')),
  ]},
  {tab:'advanced', title:'Faixa candidata de entrada', note:'Referência visual do suporte selecionado no 1H e da proximidade já configurada. Não altera score, vetos ou simulações.', fields:[
    {path:'presentation.entry_band_enabled',label:'Exibir faixa candidata de entrada',hint:'Âmbar: aguardando. Vermelho: bloqueada. Verde: gatilho confirmado. Sem dados atuais, a faixa não é desenhada.',kind:'boolean'},
  ]},
  {tab:'advanced', title:'Target · referência visual', fields:[
    field('presentation.target_pct','Valorização do Target','Curva sobre o fechamento de cada candle, antes de taxas. Não altera os alvos das operações.','%'),
  ]},
  {tab:'advanced', title:'Fear & Greed · CoinMarketCap', note:'Somente exibição do sentimento do mercado. Não altera score, vetos ou simulações. Chave em Configurações → Geral.', fields:[
    {path:'presentation.fear_greed.enabled',label:'Exibir Fear & Greed',hint:'Mostra o índice oficial da CoinMarketCap ao lado do BTC.',kind:'boolean'},
    field('presentation.fear_greed.refresh_seconds','Consulta à CoinMarketCap','Intervalo compartilhado entre abas; respeita a atualização da fonte.','segundos'),
    field('presentation.fear_greed.max_age_seconds','Validade do Fear & Greed','Idade máxima da publicação original para considerá-la atual.','segundos'),
  ]},
  {tab:'advanced', title:'Custos e simulação', fields:[
    field('fee_per_side_pct','Taxa por lado','Aplicada à entrada e à saída da simulação.','%'),
    field('slippage_pct','Slippage','Desvio de preço usado no plano simulado.','%'),
    field('capital_usdt','Capital simulado','Referência de capital para dimensionamento.','USDT'),
    field('risk_per_trade_pct','Risco por operação','Parcela do capital usada para dimensionar o risco.','%'),
    field('partial_tp1_pct','Parcial no primeiro alvo','Quantidade encerrada no primeiro alvo estrutural.','%'),
    field('expires_after_candles','Validade da entrada','Tempo para executar a entrada simulada.','candles'),
    field('holding_candles','Duração máxima','Tempo máximo de acompanhamento da simulação.','candles'),
  ]},
  {tab:'advanced', title:'Observação e relatório', fields:[
    {path:'enabled',label:'Observação MTF habilitada',hint:'Controla novas observações; simulações existentes preservam seu contrato.',kind:'boolean'},
    field('poll_seconds','Atualização do gráfico','Intervalo entre consultas.','segundos'),
    field('max_assets_per_watchlist','Limite do observador','Limite do observador legado; não restringe a lista completa do Consolidado.','ativos'),
    field('history_page_size','Página do histórico','Quantidade de avaliações por página.','itens'),
    field('confidence_level','Nível de confiança','Fração usada no intervalo descritivo do relatório.','fração'),
    field('min_report_sample','Amostra mínima','Quantidade necessária para apresentar o intervalo.','observações'),
  ]},
];
const visualFields: FieldSpec[] = [
  ...['4h','1h','5m'].map(tf=>field(`presentation.visible_candles.${tf}`,`Candles visíveis ${tf.toUpperCase()}`,'Apenas enquadramento inicial.','candles')),
  ...['price','volume','rsi','adx','other'].map((key,i)=>field(`presentation.panel_heights.${key}`,`Altura · ${['preço','volume','RSI','ADX','outros'][i]}`,'Altura do painel visual.','px')),
  ...[
    ['markers_max','Marcadores máximos','Limite total de marcadores.','marcadores'],
    ['markers_recent_signals','Sinais recentes','Prioridade para padrões recentes.','sinais'],
    ['pivots_per_type','Pivôs por tipo','Quantidade exibida de cada tipo de pivô.','pivôs'],
    ['marker_min_range_atr','Amplitude dos marcadores','Filtro exclusivamente visual.','× ATR'],
    ['zones_per_side','Zonas por lado','Quantidade de zonas exibidas em cada lado.','zonas'],
    ['removal_confirmations','Confirmações de remoção','Consultas antes de trocar um ativo removido da lista.','consultas'],
    ['retry_initial_seconds','Nova tentativa inicial','Intervalo após falha de consulta.','segundos'],
    ['retry_max_seconds','Nova tentativa máxima','Limite do intervalo após falhas.','segundos'],
    ['watchlist_poll_seconds','Atualização da watchlist','Intervalo entre consultas da lista.','segundos'],
    ['autoscale_clip_pct','Recorte visual','Fração percentual de extremos recortada quando a opção está ativa.','%'],
    ['pair_metadata_ttl_seconds','Validade dos metadados','Cache de precisão dos pares.','segundos'],
  ].map(([key,label,hint,unit])=>field(`presentation.${key}`,label,hint,unit)),
];

export function MTFSettingsDialog({config, boot, onClose, onSaved}: {config: MTFConfig | null; boot: Bootstrap; onClose:()=>void; onSaved:(config:MTFConfig)=>void}) {
  const [draft, setDraft] = useState(()=>settingsDraft(config, boot));
  const [tab, setTab] = useState<Tab>('armed');
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonText, setJsonText] = useState('');
  const [errors, setErrors] = useState<Record<string,string>>({});
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const rootSchema = boot.config_schema ?? {};
  useEffect(()=>{ const el=dialog.current; el?.showModal(); return ()=>el?.close(); },[]);
  const update = (path: string, value: unknown) => {setDraft(previous=>withConfigValue(previous,path,value));setErrors(previous=>({...previous,[path]:''}));setMessage('');};
  const save = async () => {
    let next = draft;
    try {
      if (jsonOpen) next = JSON.parse(jsonText);
      if (!next || typeof next!=='object' || Array.isArray(next)) throw new Error('A configuração deve ser um objeto JSON.');
      if (!boot.config_schema) throw new Error('Recarregue a página para obter a validação atual da configuração.');
      next = settingsForSave(next, config, boot);
      const invalid = validateSettings(next, rootSchema);
      setErrors(invalid);
      if (Object.keys(invalid).length) {setMessage('Revise os campos indicados antes de salvar.');return;}
      setSaving(true);setMessage('');
      await apiFetch('/mtf/config/validate',{method:'POST',body:JSON.stringify(next)});
      const saved = await apiFetch<MTFConfig>('/mtf/config',{method:'PUT',body:JSON.stringify(next)});
      onSaved(saved);
    } catch (e) {
      if (e instanceof ApiError && e.rawBody) {
        try {
          const detail = JSON.parse(e.rawBody).detail;
          if (Array.isArray(detail)) setErrors(Object.fromEntries(detail.map(x=>[x.loc.filter((part:unknown)=>part!=='body').join('.'),x.msg])));
        } catch { /* Preserve the server error below. */ }
      }
      setMessage(e instanceof Error ? e.message : 'Falha ao salvar configuração.');
    } finally {setSaving(false);}
  };
  const renderField = (spec: FieldSpec) => {
    if (draft.schema_version==='mtf_chart_v3' && spec.path==='atr_period') return null;
    const value = configValue(draft,spec.path);
    const inherited = spec.path==='atr_period' && value==null;
    const displayed = inherited ? (boot.inherited_atr?.enabled ? boot.inherited_atr.period : '') : value;
    const schema = schemaAt(rootSchema,spec.path);
    const id = `mtf-setting-${spec.path}`;
    const issue = errors[spec.path];
    return <div className={`mtf-setting ${issue?'invalid':''}`} key={spec.path}>
      <label htmlFor={id}>{spec.label}{spec.unit && <span>{spec.unit}</span>}</label>
      {spec.kind==='btc-timeframe' ? <select id={id} disabled={saving} value={String(value ?? '4h')} onChange={e=>update(spec.path,e.target.value)}>{(draft.schema_version==='mtf_chart_v3' ? boot.catalog?.timeframes ?? [] : ['4h','1h']).map(tf=><option key={tf} value={tf}>{tf.toUpperCase()}</option>)}</select> : spec.kind==='patterns' ? <div className="mtf-pattern-options" role="group" aria-label={spec.label}>
        {['ENGULF_BULL','PIN_BULL','MARUBOZU_BULL'].map(pattern=><label key={pattern}><input type="checkbox" disabled={saving} checked={(value as string[] ?? []).includes(pattern)} onChange={e=>update(spec.path,e.target.checked?[...(value as string[] ?? []),pattern]:(value as string[]).filter(p=>p!==pattern))}/>{patternLabels[pattern]}</label>)}
      </div> : spec.kind==='boolean' ? <select id={id} disabled={saving} value={String(value)} onChange={e=>update(spec.path,e.target.value==='true')}><option value="true">Sim</option><option value="false">Não</option></select> :
        <input id={id} disabled={saving} type="number" step={schema.type==='integer'?1:'any'} min={schema.minimum} max={schema.maximum} value={displayed==null?'':String(displayed)} aria-invalid={!!issue} aria-describedby={`${id}-hint`} onChange={e=>update(spec.path,e.target.value===''?'':Number(e.target.value))}/>}
      <small id={`${id}-hint`}>{spec.hint}{inherited && boot.inherited_atr?.enabled && <em> Valor herdado; será copiado ao salvar alterações nas regras.</em>}</small>
      {issue && <p className="mtf-field-error" role="alert">{issue}</p>}
    </div>;
  };
  const errorTab = (key: Tab) => sections.filter(s=>s.tab===key).some(s=>s.fields.some(f=>errors[f.path]));
  return <dialog ref={dialog} className="mtf-settings" aria-labelledby="mtf-settings-title" onCancel={e=>{e.preventDefault();if(!saving)onClose();}}>
    <header className="mtf-settings-header"><div><span className="mtf-settings-eyebrow">ANÁLISE MTF · CONFIGURAÇÃO</span><h2 id="mtf-settings-title">Da estrutura ao gatilho</h2><p>Regras existentes, organizadas por etapa. Alterações valem para o MTF deste usuário.</p></div><button type="button" disabled={saving} aria-label="Fechar configuração" onClick={onClose}><X size={20}/></button></header>
    <nav className="mtf-settings-tabs" aria-label="Etapas da configuração">{([
      ['armed','ARMADO',Layers],['flow','Capital e fluxo',Layers],['trigger','GATILHO',Crosshair],['vetoes','Vetos',ShieldCheck],['shared','Compartilhados',SlidersHorizontal],['advanced','Avançado',Settings2],
    ] as const).map(([key,label,Icon])=><button type="button" key={key} aria-pressed={tab===key&&!jsonOpen} onClick={()=>{if(jsonOpen){try{const parsed=JSON.parse(jsonText);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error();const invalid=validateSettings(parsed,rootSchema);if(Object.keys(invalid).length){setErrors(invalid);setMessage('Corrija os campos do JSON antes de voltar aos formulários.');return;}setDraft(parsed);setErrors({});setJsonOpen(false);setMessage('');}catch{setMessage('Corrija o JSON antes de voltar aos formulários.');return;}}setTab(key);}}><Icon size={15}/>{label}{errorTab(key)&&<span className="mtf-error-dot" aria-label="Há campos inválidos"/>}</button>)}</nav>
    <div className="mtf-settings-body">
      {!jsonOpen && <section className="mtf-settings-section">
        <button type="button" className="mtf-settings-json" disabled={saving} onClick={()=>{setJsonText(JSON.stringify(draft,null,2));setJsonOpen(true);}}>JSON completo · indicadores e regras</button>
        {boot.conversion_error && draft.schema_version!=='mtf_chart_v3' && <p role="alert">Conversão incompleta: {boot.conversion_error}. A configuração anterior permanece ativa.</p>}
        {draft.schema_version==='mtf_chart_v3' && <p>Contrato completo do MTF. Indicadores e regras próprios, sem alterar outros módulos. ATR por timeframe em frame_atr e indicators.</p>}
      </section>}
      {jsonOpen ? <section className="mtf-settings-section"><h3>JSON avançado</h3><p>A mesma configuração dos formulários. Alterações só entram em vigor ao salvar.</p><MTFJsonTools text={jsonText} onText={setJsonText} catalog={boot.catalog} disabled={saving} setBusy={setSaving} onMessage={setMessage} onErrors={setErrors}/><textarea aria-label="Configuração MTF em JSON" spellCheck={false} value={jsonText} disabled={saving} onChange={e=>setJsonText(e.target.value)}/></section> : <>
        {sections.filter(section=>section.tab===tab).map(section=><section className="mtf-settings-section" key={section.title}><h3>{section.title}</h3>{section.note&&<p>{section.title!=='Stop pelo RR bruto' && draft.schema_version==='mtf_chart_v3' && ['armed','trigger','flow','shared'].includes(section.tab) ? 'As regras efetivas desta etapa estão no JSON completo. Estes controles editam os mesmos parâmetros. Indicadores e contexto influenciam os estados quando referenciados nas regras.' : section.note}</p>}<div className="mtf-settings-grid">{section.fields.map(renderField)}</div>{section.title==='Stop pelo RR bruto' && draft.stop_sizing && Number.isFinite(draft.stop_sizing.target_pct/draft.stop_sizing.gross_rr) && <p>Distância calculada do stop: <strong>{(draft.stop_sizing.target_pct/draft.stop_sizing.gross_rr).toLocaleString('pt-BR',{maximumFractionDigits:6})}%</strong> abaixo do preço de confirmação. {draft.stop_sizing.enabled?'Aplicado aos novos planos ao salvar.':'Modo desativado; valor apenas demonstrativo.'}</p>}</section>)}
        {tab==='armed' && (draft.schema_version==='mtf_chart_v2'||draft.zone_algorithm==='mtf_chart_v2') && <section className="mtf-settings-section"><h3>Janela das zonas</h3><div className="mtf-settings-grid">{['4h','1h','5m'].map(tf=>renderField(field(`zone_lookback_candles.${tf}`,`Zonas ${tf.toUpperCase()}`,'Janela de pivôs usada na versão ativa de zonas.','candles')))}</div></section>}
        {draft.timeframes && <section className="mtf-settings-section"><h3>Timeframes das etapas</h3><p>Novos timeframes exigem candle_limits, presentation.visible_candles, indicators e frame_atr correspondentes no JSON.</p><div className="mtf-settings-grid">{(['structure','zone','trigger'] as const).map(role=><div key={role} className="mtf-setting"><label htmlFor={'mtf-role-'+role}>{({structure:'Estrutura',zone:'Zona',trigger:'Gatilho'})[role]}</label><select id={'mtf-role-'+role} value={draft.timeframes?.[role]} onChange={e=>update('timeframes.'+role,e.target.value)} disabled={saving}>{(boot.catalog?.timeframes??[]).map(tf=><option key={tf} value={tf}>{tf.toUpperCase()}</option>)}</select></div>)}</div><details><summary>Regras efetivas</summary><pre>{JSON.stringify(draft.rules,null,2)}</pre></details></section>}
        {tab==='shared' && <div className="mtf-settings-note">Os filtros de direção e Bitcoin e a validade dos dados ficam em Vetos. A análise continua usando candles fechados. Os campos compartilhados têm efeito nas duas etapas.</div>}
        {tab==='advanced' && <>
          <section className="mtf-settings-section"><h3>Watchlists</h3><div className="mtf-setting"><label htmlFor="mtf-default-watchlist">Watchlist inicial</label><select id="mtf-default-watchlist" value={draft.default_watchlist} disabled={saving} onChange={e=>update('default_watchlist',e.target.value)}>{boot.watchlists.map(w=><option value={w.id} key={w.id}>{w.name}</option>)}</select></div><fieldset className="mtf-pattern-options"><legend>Monitoradas pelo observador</legend>{boot.watchlists.map(w=><label key={w.id}><input type="checkbox" disabled={saving} checked={draft.monitored_watchlists.includes(w.id)} onChange={e=>update('monitored_watchlists',e.target.checked?[...draft.monitored_watchlists,w.id]:draft.monitored_watchlists.filter(id=>id!==w.id))}/>{w.name}</label>)}</fieldset></section>
          <details className="mtf-settings-section"><summary>Apresentação e atualização</summary><div className="mtf-settings-grid">{visualFields.map(renderField)}<div className="mtf-setting"><label htmlFor="mtf-default-indicators">Indicadores visuais iniciais</label><input id="mtf-default-indicators" disabled={saving} value={draft.presentation?.default_indicators.join(', ')??''} onChange={e=>update('presentation.default_indicators',e.target.value.split(',').map(s=>s.trim()).filter(Boolean))}/><small>Nomes separados por vírgula. A seleção pessoal de indicadores tem prioridade.</small></div></div></details>
          <button className="mtf-settings-json" type="button" onClick={()=>{setJsonText(JSON.stringify(draft,null,2));setJsonOpen(true);}}>Editar JSON avançado</button>
          <button className="mtf-settings-json" type="button" disabled={saving} onClick={()=>{setDraft(settingsDraft(null,boot));setErrors({});setMessage('Preset carregado no formulário. Salve para aplicar ou cancele para descartar.');}}>Carregar preset experimental</button>
        </>}
      </>}
      {message && <div className="mtf-error" role="alert">{message}</div>}
      {Object.entries(errors).filter(([,message])=>message).length>0 && <ul className="mtf-settings-errors">{Object.entries(errors).filter(([,text])=>text).map(([path,text])=><li key={path}>{[...sections.flatMap(s=>s.fields),...visualFields].find(f=>f.path===path)?.label??path}: {text}</li>)}</ul>}
    </div>
    <footer className="mtf-settings-footer"><p>Salvar regras atualiza as próximas avaliações do Consolidado e pode alterar a elegibilidade de novas simulações. Histórico preservado.</p><div><button type="button" disabled={saving} onClick={onClose}>Cancelar</button><button type="button" className="mtf-primary" disabled={saving} onClick={save}>{saving?'Salvando…':'Salvar configuração'}</button></div></footer>
  </dialog>;
}
