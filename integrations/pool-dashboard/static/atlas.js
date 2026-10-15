'use strict';
const $=s=>document.querySelector(s), content=$('#content');
const state={health:null,reports:[],runs:[],report:null,run:null};
// API pagination uses IDs; editorial recency follows day and numeric revision.
function compareEditions(a,b){return a.day.localeCompare(b.day)||(a.revision||0)-(b.revision||0)||a.id.localeCompare(b.id);}
const labels={SUCCEEDED:'Concluído',BLOCKED:'Bloqueado',PENDING:'Pendente',FAILED:'Falhou',INVALID:'Rejeitado na validação',INTERRUPTED:'Interrompido',RUNNING:'Em execução',REVIEW_REQUIRED:'Revisão necessária',PUBLISHED_PARTIAL:'Publicado · cobertura parcial',UNKNOWN:'Não determinado',STALE:'Desatualizado',PARTIAL:'Parcial',PROPOSED:'Proposta',EXPIRED:'Expirada'};
const problems={ANTHROPIC_KEY_MISSING:'Chave Anthropic ainda não configurada.',ANTHROPIC_AUTH_REJECTED:'A Anthropic recusou a credencial configurada.',DAILY_BUDGET_REQUIRED:'Orçamento diário não configurado.',MODEL_UNAVAILABLE:'Modelo indisponível para esta conta.',MANAGED_AGENTS_ACCESS_REQUIRED:'A conta precisa de acesso a Managed Agents.',PREVIOUS_SESSION_UNCERTAIN:'A execução anterior precisa ser conferida antes de novas chamadas.',SCHEMA_OR_POLICY_REJECTED:'O parecer não passou pela validação de evidências e contrato.'};
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);if(cls)n.className=cls;return n;}
function add(parent,...children){children.filter(Boolean).forEach(c=>parent.append(c));return parent;}
function link(text,url,external=false){const a=el('a',text);a.href=url;if(external){a.target='_blank';a.rel='noopener noreferrer';}return a;}
function badge(text){return el('span',labels[text]||text||'NÃO DISPONÍVEL','badge');}
function date(t){return t?new Date(t).toLocaleString('pt-BR',{timeZone:'America/Sao_Paulo'}):'NÃO DISPONÍVEL';}
function pretty(value){return value===null||value===undefined?'NÃO DISPONÍVEL':String(value);}
function json(value,title='Dados completos'){const d=el('details');add(d,el('summary',title),el('pre',JSON.stringify(value,null,2)));return d;}
function prose(text){return el('p',text,'prose');}
function params(){return new URLSearchParams(location.search);}
function route(path){const q=params();q.delete('q');q.delete('risk');q.delete('source');return path+(q.size?'?'+q:'');}
function notice(text,bad=false){return el('p',text,'warning'+(bad?' bad':''));}
function empty(title,text){return add(el('div',null,'empty'),el('h2',title),prose(text));}
async function api(path){const r=await fetch('/api/v1/atlas'+path,{credentials:'same-origin'});if(r.status===401){showLogin();throw new Error('Entre para consultar o ATLAS.');}if(!r.ok)throw new Error(r.status===404?'Nenhum relatório validado disponível.':'Consulta indisponível. Tente novamente.');return r.json();}
async function pages(path){let out=[],cursor='';do{const d=await api(path+'?limit=100&cursor='+encodeURIComponent(cursor));out.push(...d.items);cursor=d.next_cursor;}while(cursor);return out;}
function showLogin(){$('#workspace').hidden=true;$('#login').hidden=false;$('#loading').hidden=true;}
function err(e){$('#error').textContent=e.message;$('#error').hidden=false;$('#loading').hidden=true;}
function evidenceLinks(ids){const wrap=el('div');for(const id of ids||[]){const a=link(id.slice(0,22)+'…',route('/atlas/evidencias/'+encodeURIComponent(id)));a.className='source-link';wrap.append(a);}return wrap;}
function heading(kicker,title,description){add(content,el('p',kicker,'kicker'),el('h1',title),description?el('p',description,'deck'):null);}
function opinions(){return state.report?.agents||state.run?.agents||state.health.agents.map(code=>({code,status:'PENDING'}));}
function evidence(){return state.report?.evidence||state.run?.evidence||[];}
function quant(){return state.report?.quant||state.run?.quant;}
function renderFinding(f){return add(el('article',null,'finding'),badge(f.risk_level),badge(f.occurrence),el('h3',f.title),prose(f.explanation),
  el('p','Impacto esperado: '+f.expected_impact),el('p','Impacto observado: '+pretty(f.observed_impact)),el('p','Causalidade: '+f.causality_status,'meta'),
  el('p','Associação: '+f.association+' · '+f.scope,'meta'),f.scheduled_at?el('p','Programado: '+date(f.scheduled_at)):null,evidenceLinks(f.evidence_ids));}
function overview(){
 const r=state.report, q=quant();heading('EDIÇÃO DIÁRIA · INTELIGÊNCIA COM EVIDÊNCIAS',r?.editorial.title||'Uma leitura do mercado.\nCada conclusão, rastreável.',r?.editorial.summary||'Acompanhe cada especialista, a revisão do AUDITOR e a síntese final do ATLAS.');
 const hero=el('section',null,'hero'),lead=el('div'),aside=el('aside');
 add(lead,el('p','ESTADO DA EDIÇÃO','kicker'),badge(r?.report_status||state.run?.status||'PENDING'),notice('Somente análise e propostas. Nenhuma decisão deste portal executa ordens, altera scores ou configurações do Scalpyn.'));
 for(const [value,label] of [[pretty(q?.observed_watchlist_total),'Ativos observados na watchlist'],[pretty(q?.pool_total),'Membros ativos da POOL'],[pretty(state.health.published_reports),'Relatórios publicados']])add(aside,add(el('div',null,'stat'),el('strong',value),el('small',label)));
 add(content,add(hero,lead,aside));
 if(!r)add(content,empty('Síntese final ainda não publicada.',state.run?'Consulte os pareceres e os bloqueios desta rodada. Uma falha ou falta de evidência não será apresentada como conclusão de IA.':'O ciclo diário ainda não foi executado. Os snapshots são a fonte quantitativa, não uma análise de IA.'));
 if(!r&&state.health.last_successful_report)add(content,link('Último relatório publicado · '+date(state.health.last_successful_report.generated_at),'/atlas?report='+state.health.last_successful_report.id));
 if(r){add(content,el('h2','Parecer ATLAS'),prose(r.editorial.analysis));for(const l of r.editorial.limitations)add(content,notice(l));}
 const section=el('div',null,'section-head');add(section,el('h2','A mesa de especialistas'),link('Todos os pareceres →',route('/atlas/agentes')));content.append(section);agentCards();
 add(content,el('h2','Acontecimentos em evidência'));
 const findings=opinions().filter(a=>a.code!=='ATLAS').flatMap(a=>a.output?.findings||[]);
 if(!findings.length)content.append(prose('Nenhum acontecimento interpretado e validado nesta edição. Consulte as fontes coletadas e seu estado de disponibilidade.'));
 findings.slice(0,5).forEach(f=>content.append(renderFinding(f)));
 const upcoming=findings.filter(f=>f.occurrence==='UPCOMING');add(content,el('h2','Agenda informada pelas fontes'));
 if(!upcoming.length)content.append(prose('Agenda verificada não disponível nesta edição. Ausência de registros não significa ausência de eventos.'));else upcoming.forEach(f=>content.append(renderFinding(f)));
}
function agentCards(){const grid=el('div',null,'agent-grid');for(const a of opinions()){const card=el('article',null,'agent-card');add(card,el('p','PARECER INDIVIDUAL','kicker'),link(a.code+' →',route('/atlas/agentes/'+a.code)),el('p',null),badge(a.status),el('p',a.output?.summary||problems[a.error_code]||a.error_code||'Aguardando execução.','muted'),el('p',a.model|| (a.code==='QUANT'?'Python · determinístico':'Modelo não executado'),'meta'));grid.append(card);}content.append(grid);}
function agent(code){heading('PARECERES · AUDITORIA · SÍNTESE',code||'Todos os entregáveis.','Leitura integral das saídas e das limitações de cada etapa.');if(!code){agentCards();return;}
 const a=opinions().find(a=>a.code===code);if(!a){content.append(empty('Agente não encontrado.','Selecione um parecer da edição.'));return;}
 add(content,badge(a.status),el('p','Modelo: '+pretty(a.model)+' · início: '+date(a.started_at)+' · término: '+date(a.finished_at),'meta'));
 if(a.error_code)content.append(notice(problems[a.error_code]||a.error_code,true));
 add(content,json({model:a.model,prompt_hash:a.prompt_hash,session_id:a.session_id,usage:a.usage,reserved_cents:a.reserved_cents},'Execução, versão e consumo'));
 const o=a.output;if(!o){content.append(empty('Parecer não produzido.','Este estado não constitui uma análise ou recomendação.'));return;}
 add(content,el('h2',o.title),prose(o.summary),prose(o.analysis));
 (o.findings||[]).forEach(f=>content.append(renderFinding(f)));
 if(o.audit_issues?.length){content.append(el('h2','Inconsistências apontadas'));o.audit_issues.forEach(x=>content.append(notice(x,true)));}
 (o.limitations||[]).forEach(x=>content.append(notice(x)));
 if(a.code==='QUANT')content.append(link('Ver todos os ativos e indicadores →',route('/atlas/ativos')));
 add(content,json(o,'Entregável completo em JSON'));
}
function filterBar(fields){const form=el('form',null,'filters'),p=params();for(const [key,title,choices] of fields){const label=el('label',title);let input;if(choices){input=el('select');for(const v of ['',...choices]){const o=el('option',v||'Todos');o.value=v;input.append(o);}input.value=p.get(key)||'';}else{input=el('input');input.value=p.get(key)||'';input.placeholder='Pesquisar';}input.name=key;label.append(input);form.append(label);}form.append(el('button','Filtrar'));form.addEventListener('submit',e=>{e.preventDefault();for(const [k,v]of new FormData(form)){if(v)p.set(k,v);else p.delete(k);}history.replaceState(null,'',location.pathname+'?'+p);render();});content.append(form);}
function assets(id){const q=quant(),rows=q?.assets||[],risks=state.report?.editorial.asset_risks||[];heading('UNIVERSO OBSERVADO',id?(rows.find(a=>a.asset_id===id)?.symbol||'Ativo'):'Ativos & exposição.','Membros da edição e dados quantitativos preservados, incluindo ativos sem indicadores.');content.append(notice(q?.pool_membership_status==='COMPLETE'?'Todos os membros ativos da POOL foram recebidos. Cobertura de membros não equivale a cobertura completa de riscos.':'Cobertura da POOL não comprovada nesta edição histórica.'));if(q?.pool_membership_status==='COMPLETE')content.append(json({pool_total:q.pool_total,pool_row_count:q.pool_row_count,watchlist_total:q.observed_watchlist_total,matched:q.matched,pool_only:q.pool_only,watchlist_only:q.watchlist_only,inactive:q.pool_inactive},'Reconciliação POOL × watchlist'));
 if(id){const a=rows.find(a=>a.asset_id===id);if(!a){content.append(empty('Ativo ausente nesta edição.','Consulte outra data no histórico.'));return;}const risk=risks.find(r=>r.asset_id===id);add(content,badge(a.coverage_status),el('p','Atualização quantitativa: '+date(a.refreshed_at),'meta'),risk?prose(risk.explanation):notice('Risco não determinado: parecer ATLAS não disponível.'),json(a.decision_snapshot,'Snapshot da decisão histórica do Scalpyn'),json(a.indicator_readings,'Indicadores persistidos por timeframe'),json(a.freshness_by_reading,'Atualidade calculada por leitura'),json(a.identity,'Identidade comprovada pela exchange'),json(a,'Identidade e dados quantitativos'));
  const fs=opinions().flatMap(p=>p.output?.findings||[]).filter(f=>f.asset_ids.includes(id));fs.forEach(f=>content.append(renderFinding(f)));return;}
 filterBar([['q','Símbolo'],['risk','Risco',['CRITICAL','HIGH','MEDIUM','LOW','UNKNOWN','NO_ALERT']]]);
 const table=el('table'),head=el('tr');for(const h of ['Ativo','Cobertura','Risco','Preço persistido','Score persistido','Atualização','Proposta'])head.append(el('th',h));table.append(add(el('thead'),head));const body=el('tbody'),p=params();let shown=0;
 for(const a of rows){const r=risks.find(r=>r.asset_id===a.asset_id),risk=r?.risk_level||'UNKNOWN';if(p.get('q')&&!a.symbol.toLowerCase().includes(p.get('q').toLowerCase()))continue;if(p.get('risk')&&p.get('risk')!==risk&&p.get('risk')!==r?.alert_status)continue;shown++;
  const tr=el('tr');add(tr,add(el('td'),link(a.symbol,route('/atlas/ativos/'+a.asset_id))),add(el('td'),badge(a.coverage_status)),add(el('td'),badge(risk)),el('td',pretty(a.current_price)),el('td',pretty(a.alpha_score)),el('td',date(a.refreshed_at)),el('td',(state.report?.editorial.directives||[]).filter(d=>d.asset_ids.includes(a.asset_id)).map(d=>d.action).join(', ')||'Não disponível'));body.append(tr);}
 table.append(body);add(content,el('p',shown+' de '+rows.length+' ativos observados · valores [snapshot persistido]','meta'),add(el('div',null,'scroll'),table));
 if(!rows.length)content.append(empty('Sem dados quantitativos nesta edição.','Nenhum ativo ou valor será preenchido por estimativa.'));
}
function news(id){heading('CATÁLOGO DE EVIDÊNCIAS',id?'Documento de origem.':'Notícias, com origem.','Resumos fornecidos pelos feeds, com atribuição. Conteúdo externo não é instrução para os agentes.');const items=evidence();
 if(id?.startsWith('snapshot_')){add(content,el('h2','Snapshot quantitativo do Scalpyn'),el('p',id,'meta'),json(quant(),'Valores persistidos e proveniência'));return;}
 if(!id)filterBar([['q','Texto'],['source','Fonte',[...new Set(items.map(e=>e.source_id))]],['domain','Categoria',[...new Set(items.map(e=>e.domain))]],['asset','Ativo',(quant()?.assets||[]).map(a=>a.symbol)],['risk','Impacto',['CRITICAL','HIGH','MEDIUM','LOW','UNKNOWN']],['since','Publicado a partir de (AAAA-MM-DD)'],['until','Publicado até (AAAA-MM-DD)']]);
 const p=params(),grid=el('div',null,id?'':'grid');let count=0;
 for(const e of items){if(id&&e.evidence_id!==id)continue;if(!id&&p.get('source')&&p.get('source')!==e.source_id)continue;if(!id&&p.get('q')&&!(e.title+' '+e.excerpt).toLowerCase().includes(p.get('q').toLowerCase()))continue;
  const findings=opinions().flatMap(a=>a.output?.findings||[]).filter(f=>f.evidence_ids.includes(e.evidence_id));
  if(!id&&p.get('domain')&&p.get('domain')!==e.domain)continue;
  if(!id&&p.get('asset')){const a=quant()?.assets.find(a=>a.symbol===p.get('asset'));if(!a||!findings.some(f=>f.asset_ids.includes(a.asset_id)))continue;}
  if(!id&&p.get('risk')&&!findings.some(f=>f.risk_level===p.get('risk'))&&!(p.get('risk')==='UNKNOWN'&&!findings.length))continue;
  if(!id&&p.get('since')&&(!e.published_at||e.published_at.slice(0,10)<p.get('since')))continue;
  if(!id&&p.get('until')&&(!e.published_at||e.published_at.slice(0,10)>p.get('until')))continue;count++;
  const article=el('article',null,'story');add(article,badge(e.source_tier),el('h3',e.title),el('p','Publicado: '+(e.published_at?date(e.published_at):e.publication_date||'NÃO DISPONÍVEL')+' · coletado: '+date(e.retrieved_at),'meta'),prose(e.excerpt),el('p','Documento ou trecho da origem. Classificação primária identifica o emissor; não garante cobertura universal nem confirmação independente.','muted'));
  const actions=el('div',null,'actions');if(/^https:\/\//.test(e.url))actions.append(link('Fonte original ↗',e.url,true));if(!id)actions.append(link('Evidência & vínculos →',route('/atlas/evidencias/'+e.evidence_id)));article.append(actions);
  if(id){add(article,json(e,'Metadados e versão'));opinions().flatMap(a=>a.output?.findings||[]).filter(f=>f.evidence_ids.includes(id)).forEach(f=>article.append(renderFinding(f)));}grid.append(article);
 }content.append(grid);if(!count)content.append(empty('Nenhuma evidência disponível neste filtro.','Consulte o estado das fontes em Operação. Ausência de notícia não significa ausência de risco.'));
}
function decisions(){heading('CONCLUSÃO CONSOLIDADA', 'Decisões finais do ATLAS.','A conclusão editorial é final para esta edição. Toda diretiva operacional permanece apenas uma proposta em SHADOW_ONLY.');
 if(!state.report){const atlas=opinions().find(a=>a.code==='ATLAS');content.append(empty('Nenhuma decisão aprovada para publicação.','A publicação exige auditoria favorável e validação de evidências. A conclusão do ciclo, inclusive seu veto, permanece abaixo.'));if(atlas?.output){if(atlas.status!=='SUCCEEDED')content.append(notice('A síntese ATLAS foi rejeitada na validação. A saída recebida está preservada abaixo, mas não constitui uma decisão final válida.',true));add(content,badge(state.run?.status),el('h2',atlas.output.title),prose(atlas.output.summary),prose(atlas.output.analysis),link('Parecer ATLAS integral →',route('/atlas/agentes/ATLAS')),link('Auditoria integral →',route('/atlas/agentes/AUDITOR')),json(atlas.output,'Conclusão completa, não aprovada'));}return;}
 add(content,prose(state.report.editorial.summary),prose(state.report.editorial.analysis));
 for(const d of state.report.editorial.directives){const expired=new Date(d.expires_at)<=new Date();const card=el('article',null,'story');add(card,badge(expired?'EXPIRED':'PROPOSED'),badge('SHADOW_ONLY'),el('h2',d.action),prose(d.reason),el('p','Alcance: '+d.scope+' · validade: '+date(d.expires_at),'meta'),evidenceLinks(d.evidence_ids),json(d,'Diretiva completa'));content.append(card);}
 if(!state.report.editorial.directives.length)content.append(notice('A síntese não emitiu diretivas nesta edição. Isso não constitui autorização para trading.'));
 add(content,notice('Aprovações e aplicação real não estão habilitadas. Feedback do consumidor é aceito somente como SIMULATED ou REJECTED no ambiente shadow.'));
}
function historyPage(){heading('MEMÓRIA EDITORIAL','Histórico, sem reescrita.','Relatórios publicados são imutáveis. Ciclos bloqueados e saídas rejeitadas permanecem consultáveis.');
 for(const r of [...state.runs].reverse()){const report=state.reports.find(x=>x.run_id===r.id);const article=el('article',null,'story');add(article,badge(r.status),el('h2',r.day),el('p','Início: '+date(r.started_at)+' · conclusão: '+date(r.finished_at),'meta'),link('Abrir ciclo completo →','/atlas?run='+encodeURIComponent(r.id)));if(report){const a=el('div',null,'actions');add(a,link('Relatório editorial','/atlas?report='+report.id),link('JSON','/api/v1/atlas/reports/'+report.id+'/export?format=json'),link('Markdown','/api/v1/atlas/reports/'+report.id+'/export?format=md'));article.append(a);}if(r.error_code)article.append(notice(r.error_code));content.append(article);}
 if(!state.runs.length)content.append(empty('O histórico começa na primeira execução.','Não há relatórios retroativos produzidos por estimativa.'));
 const reports=state.reports;if(reports.length>=2){const f=el('form',null,'filters');for(const name of ['before','after']){const s=el('select');s.name=name;s.setAttribute('aria-label',name==='before'?'Relatório anterior':'Relatório posterior');reports.forEach(r=>{const o=el('option',r.id);o.value=r.id;s.append(o);});f.append(s);}f.append(el('button','Comparar relatórios'));const target=el('div');f.addEventListener('submit',async e=>{e.preventDefault();const data=new FormData(f);try{const [a,b]=await Promise.all([api('/reports/'+data.get('before')),api('/reports/'+data.get('after'))]);target.replaceChildren();const ids=new Set([...a.editorial.asset_risks,...b.editorial.asset_risks].map(x=>x.asset_id));for(const id of ids){const x=a.editorial.asset_risks.find(v=>v.asset_id===id),y=b.editorial.asset_risks.find(v=>v.asset_id===id);target.append(el('p',id+': '+(x?.risk_level||'ausente')+' → '+(y?.risk_level||'ausente')));}target.append(notice('Comparação descritiva de pareceres. Não mede causalidade ou acurácia preditiva.'));}catch(e){err(e);}});add(content,f,target);}
}
function operation(){heading('CONTROLE OPERACIONAL','O que foi executado.','Estado por origem, uso dos modelos, bloqueios e trilha de publicação.');add(content,json(state.health,'Configuração e saúde do ATLAS'),notice('Limite diário configurado: US$ '+pretty(state.health.daily_budget_usd)+'. Novos ciclos usam uma chamada por agente, contagem prévia de entrada e limite de tokens de saída. Revisões compartilham o orçamento diário. Custos calculados são identificados na execução; incerteza bloqueia novas chamadas.'));
 for(const s of state.report?.source_health||state.run?.source_health||[])add(content,add(el('article',null,'story'),badge(s.status),el('h3',s.source_id),el('p',date(s.retrieved_at),'meta'),json(s,'Resposta da origem')));
 agentCards();add(content,json(state.run?{...state.run,agents:undefined,evidence:undefined,quant:undefined}:null,'Ciclo e limitações'));
}
function render(){content.replaceChildren();const path=location.pathname.split('/').filter(Boolean),section=path[1]||'';document.querySelectorAll('nav a').forEach(a=>{a.classList.toggle('active',new URL(a.href).pathname==='/atlas'+(section?'/'+section:''));a.href=route(new URL(a.href).pathname);});
 $('#edition-date').textContent=state.report?'Publicado '+date(state.report.generated_at):'Ciclo '+(state.run?.day||'ainda não iniciado');
 if(state.report&&new Date(state.report.expires_at)<=new Date())content.append(notice('Relatório vencido: disponível como histórico, sem elegibilidade para novas diretivas.',true));
 if(section==='agentes')agent(path[2]);else if(section==='ativos')assets(path[2]);else if(section==='noticias'||section==='evidencias')news(path[2]);else if(section==='decisoes')decisions();else if(section==='historico')historyPage();else if(section==='operacao')operation();else overview();
}
async function load(){try{$('#error').hidden=true;state.health=await api('/health');[state.runs,state.reports]=await Promise.all([pages('/runs'),pages('/reports')]);state.runs.sort(compareEditions);const p=params();const reportId=p.get('report');let runId=p.get('run');state.report=null;state.run=null;
 if(reportId){state.report=await api('/reports/'+encodeURIComponent(reportId));runId=state.report.run_id;}else if(!runId){runId=state.runs.at(-1)?.id;const r=state.reports.find(x=>x.run_id===runId);if(r)state.report=await api('/reports/'+r.id);}
 if(runId){state.run=await api('/runs/'+encodeURIComponent(runId));if(!state.report){const published=state.reports.find(r=>r.run_id===runId);if(published)state.report=await api('/reports/'+published.id);}}
 const select=$('#edition-select');select.replaceChildren();if(!state.runs.length){select.append(el('option','Aguardando primeiro ciclo'));}for(const r of [...state.runs].reverse()){const option=el('option',r.day+(r.revision?' · revisão '+r.revision:' · original')+' · '+(labels[r.status]||r.status));option.value=r.id;select.append(option);}if(runId)select.value=runId;
 $('#loading').hidden=true;$('#login').hidden=true;$('#workspace').hidden=false;render();
 }catch(e){err(e);}}
$('#edition-select').addEventListener('change',()=>{const p=params();p.delete('report');p.set('run',$('#edition-select').value);location.search=p.toString();});
$('#refresh').addEventListener('click',load);
$('#logout').addEventListener('click',async()=>{await fetch('/api/logout',{method:'POST'});showLogin();});
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);const button=form.querySelector('button');button.disabled=true;try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(data))});form.reset();if(!r.ok)throw new Error('Não foi possível autenticar esta conta.');await load();}catch(e){err(e);}finally{button.disabled=false;}});
load();
