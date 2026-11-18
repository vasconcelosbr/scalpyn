// Local browser acceptance with synthetic market data; no production API calls.
// MTF_SCHEMA_PATH: JSON containing preset, presentation_preset, config_schema.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const seed=JSON.parse(fs.readFileSync(process.env.MTF_SCHEMA_PATH,'utf8'));
const output=process.env.MTF_SCREENSHOT_DIR || path.resolve('.codex-test/mtf-target-settings');
fs.mkdirSync(output,{recursive:true});
let config={...seed.preset,poll_seconds:300,presentation:{...seed.presentation_preset,target_pct:1}};
let preferences={theme:'dark',timezone:'UTC',sound:false,aligned_only:false,enabled_indicators:['ema9'],last_symbol:'TEST_USDT',last_watchlist:'l3-consolidated',last_timeframe:'5m'};
let writes=0, priceChange=0, failSave=false;
const now=1794990600;
const analysis={symbol:'TEST_USDT',status:'ARMED',observed_at_utc:new Date(now*1000).toISOString(),evaluated_at_utc:new Date(now*1000).toISOString(),
  config_hash:'fixture',mode:'SHADOW',operational_effect:false,context_4h:{trend:'UP',last_pivots:['HL','HH'],strength:'NEUTRAL',last_candle_pattern:null},
  zone_1h:null,zones:[],trigger_5m:null,plan:null,score:{total:4,raw:4,maximum:10,components:{}},vetoes:[],explanation:[],unavailable_profile_indicators:[],spread_pct:.01,btc_trend:'UP'};
function chart(symbol){
  const scale=symbol==='TEST_USDT'?1:.00004;
  return {symbol,analysis:{...analysis,symbol},profile:null,as_of:analysis.observed_at_utc,indicator_config:{},price_format:{precision:8,min_move:.00000001,source:'synthetic'},frames:Object.fromEntries(Object.entries({'4h':14400,'1h':3600,'5m':300}).map(([tf,step])=>{
    const candles=Array.from({length:100},(_,i)=>{const close=(100+Math.sin(i/7)*.6+i*.012+(i===99?priceChange:0))*scale;return {time:now-(99-i)*step,open:close*.999,high:close*1.002,low:close*.997,close,volume:100+i,closed:i!==99};});
    return [tf,{candles,indicators:{ema9:candles.map(c=>({time:c.time,value:c.close*.999}))},patterns:[],pivots:[],zones:[],source:'synthetic',stale:false}];
  }))};
}
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.MTF_BROWSER_PATH?{executablePath:process.env.MTF_BROWSER_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'pt-BR'});
  const page=await context.newPage(); const errors=[]; const checks=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.addInitScript(()=>{localStorage.setItem('token','SYNTHETIC_LOCAL_TEST');localStorage.setItem('user',JSON.stringify({id:'fixture',email:'fixture@example.invalid'}));});
  await page.route('**/api/**',async route=>{
    const p=new URL(route.request().url()); let body={};let status=200;
    if(p.pathname==='/api/mtf/bootstrap') body={...seed,config,preferences,watchlists:[{id:'l3-consolidated',name:'L3 Consolidado'}],profiles:[]};
    else if(p.pathname==='/api/mtf/preferences'){preferences=JSON.parse(route.request().postData());body=preferences;}
    else if(p.pathname==='/api/mtf/config'){
      writes++; if(failSave){status=503;body={detail:'Falha de teste; configuração anterior preservada'};}
      else {config=JSON.parse(route.request().postData());body=config;}
    }
    else if(p.pathname==='/api/mtf/watchlist')body={items:['TEST_USDT','SMALL_USDT'].map(symbol=>({symbol,current_price:chart(symbol).frames['5m'].candles.at(-1).close,mtf:{...analysis,symbol}})),limited:false};
    else if(p.pathname==='/api/mtf/chart')body=chart(p.searchParams.get('symbol'));
    else if(p.pathname==='/api/mtf/history')body={items:[],next_cursor:null};
    else if(p.pathname==='/api/mtf/report')body={groups:[]};
    else if(p.pathname==='/api/auth/me')body={id:'fixture',email:'fixture@example.invalid'};
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  });
  const open=()=>page.getByRole('button',{name:'Configurar análise MTF'}).click();
  const save=()=>page.getByRole('button',{name:'Salvar configuração',exact:true}).click();
  const shot=name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
  try{
    await page.goto(process.env.MTF_BASE_URL || 'http://127.0.0.1:5000/grafico-mtf');
    await page.locator('.mtf-chart-scroll canvas').first().waitFor();
    assert.equal(await page.getByRole('button',{name:'Target1%',exact:true}).getAttribute('aria-pressed'),'true');
    assert((await page.locator('.mtf-target-value').innerText()).includes('Target1%'));
    await page.locator('.mtf-chart-scroll canvas').first().evaluate(el=>window.__targetCanvas=el);
    for(const tf of ['4H','1H','5M']){await page.locator('.mtf-timeframes').getByRole('button',{name:tf,exact:true}).click();await page.locator('.mtf-target-value').waitFor();}
    assert(await page.locator('.mtf-chart-scroll canvas').first().evaluate(el=>el===window.__targetCanvas));
    await page.locator('.mtf-asset').filter({hasText:'SMALL/USDT'}).click();
    await page.waitForFunction(()=>document.querySelector('.mtf-chart-heading strong')?.textContent.includes('SMALL/USDT'));
    await page.locator('.mtf-asset').filter({hasText:'TEST/USDT'}).click();
    await page.waitForFunction(()=>document.querySelector('.mtf-chart-heading strong')?.textContent.includes('TEST/USDT'));
    await page.locator('.mtf-chart-scroll canvas').first().evaluate(el=>window.__targetCanvas=el);
    const prior=await page.locator('.mtf-target-value').innerText(); priceChange=2;
    await page.getByRole('button',{name:'Atualizar',exact:true}).click();
    await page.waitForFunction(previous=>document.querySelector('.mtf-target-value')?.textContent!==previous,prior);
    assert(await page.locator('.mtf-chart-scroll canvas').first().evaluate(el=>el===window.__targetCanvas));
    checks.push('Target follows symbols and live candles; timeframe changes and updates preserve the canvas');
    await shot('target-dark');
    await page.getByRole('button',{name:'Target1%',exact:true}).click();
    await page.reload();await page.locator('.mtf-chart-scroll canvas').first().waitFor();
    assert.equal(await page.getByRole('button',{name:'Target1%',exact:true}).getAttribute('aria-pressed'),'false');
    await page.getByRole('button',{name:'Target1%',exact:true}).click();
    checks.push('Target visibility persists with an existing EMA preference');
    await open();await shot('settings-armed');
    await page.getByLabel('Proximidade para armar',{exact:false}).fill('0.9');
    await page.getByRole('button',{name:'Cancelar',exact:true}).click();assert.equal(writes,0);
    await open();await page.getByRole('button',{name:'GATILHO',exact:true}).click();
    await shot('settings-trigger');
    await page.getByLabel('Score mínimo',{exact:false}).fill('999');await save();assert.equal(writes,0);
    assert(await page.getByText('O score mínimo excede a soma dos pesos.',{exact:false}).count()>0);
    await page.getByLabel('Score mínimo',{exact:false}).fill(String(seed.preset.min_score));
    await page.getByRole('button',{name:'Avançado',exact:true}).click();
    await page.getByLabel('Valorização do Target',{exact:false}).fill('2');await save();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    assert.equal(config.atr_period,null);assert.equal(config.presentation.target_pct,2);
    await page.getByRole('button',{name:'Target2%',exact:true}).waitFor();
    checks.push('Cancel and invalid input do not save; visual-only save preserves inherited engine config');
    await open();await page.getByRole('button',{name:'Compartilhados',exact:true}).click();
    await page.getByLabel('Período do ATR',{exact:false}).fill('21');await save();
    await page.getByRole('dialog').waitFor({state:'hidden'});assert.equal(config.atr_period,21);
    await page.reload();await page.locator('.mtf-chart-scroll canvas').first().waitFor();await open();
    await page.getByRole('button',{name:'Compartilhados',exact:true}).click();
    assert.equal(await page.getByLabel('Período do ATR',{exact:false}).inputValue(),'21');
    await page.getByRole('button',{name:'Avançado',exact:true}).click();await page.getByRole('button',{name:'Editar JSON avançado'}).click();
    const json=JSON.parse(await page.getByLabel('Configuração MTF em JSON').inputValue());json.zone_proximity_atr=.85;
    await page.getByLabel('Configuração MTF em JSON').fill(JSON.stringify(json));
    await page.getByRole('button',{name:'ARMADO',exact:true}).click();
    assert.equal(await page.getByLabel('Proximidade para armar',{exact:false}).inputValue(),'0.85');
    failSave=true;await save();await page.getByText(/Falha de teste/).waitFor();
    assert.notEqual(config.zone_proximity_atr,.85);failSave=false;await save();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    checks.push('Local ATR and JSON edits round-trip; failed writes retain prior configuration');
    await page.getByRole('button',{name:'Alternar tema do ambiente MTF'}).click();await open();await shot('settings-light');
    await page.getByRole('button',{name:'Cancelar',exact:true}).click();await shot('target-light');
    await page.setViewportSize({width:390,height:844});await open();await shot('settings-mobile');
    const bounds=await page.getByRole('dialog').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=390);
    await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
    checks.push('Light theme, mobile dialog and keyboard dismissal work');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({checks,errors,source:'Synthetic local fixtures; mocked API writes only'},null,2));
    console.log(JSON.stringify({checks,errors}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
