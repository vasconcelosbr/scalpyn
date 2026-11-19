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
  return {symbol,analysis:{...analysis,symbol,pump_flow:config.pump_flow?.enabled?{confirmation:'unavailable',config_hash:'fixture-flow',capital:{},asset:{state:'unavailable'},reasons:['pump_snapshot_stale_or_missing']}:undefined},profile:null,as_of:analysis.observed_at_utc,indicator_config:{},price_format:{precision:8,min_move:.00000001,source:'synthetic'},frames:Object.fromEntries(Object.entries({'4h':14400,'1h':3600,'5m':300}).map(([tf,step])=>{
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
    if(p.pathname==='/api/mtf/bootstrap') body={...seed,inherited_atr:seed.inherited_atr??{enabled:true,period:14},config,preferences,watchlists:[{id:'l3-consolidated',name:'L3 Consolidado'}],profiles:[]};
    else if(p.pathname==='/api/mtf/preferences'){preferences=JSON.parse(route.request().postData());body=preferences;}
    else if(p.pathname==='/api/mtf/config'){
      writes++; if(failSave){status=503;body={detail:'Falha de teste; configuração anterior preservada'};}
      else {config=JSON.parse(route.request().postData());body=config;}
    }
    else if(p.pathname==='/api/mtf/watchlist')body={items:['TEST_USDT','SMALL_USDT'].map(symbol=>({symbol,current_price:chart(symbol).frames['5m'].candles.at(-1).close,mtf:{...analysis,symbol}})),limited:false};
    else if(p.pathname==='/api/mtf/chart')body=chart(p.searchParams.get('symbol'));
    else if(p.pathname==='/api/mtf/history')body={items:[],next_cursor:null};
    else if(p.pathname==='/api/mtf/pump-flow/report')body={groups:[],status:'collecting'};
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
    await open();await page.getByRole('button',{name:'Capital e fluxo',exact:true}).click();
    await shot('flow-settings');
    const before=structuredClone(config);
    await page.getByLabel('Ativar contexto e comparação',{exact:true}).selectOption('true');
    await page.getByLabel('Delta normalizado mínimo',{exact:false}).fill('0.07');
    await save();await page.getByRole('dialog').waitFor({state:'hidden'});
    assert.equal(config.pump_flow.enabled,true);assert.equal(config.pump_flow.asset_min_delta,.07);
    const {pump_flow,...after}=config;const {pump_flow:oldFlow,...baseline}=before;assert.deepEqual(after,baseline);
    await page.reload();await page.locator('.mtf-chart-scroll canvas').first().waitFor();
    await page.getByRole('region',{name:'Capital e fluxo Pump'}).getByText('CAPITAL 4H',{exact:true}).waitFor();
    await page.getByText('Comparação Shadow · mesma execução',{exact:true}).click();
    await page.getByText('Coletando novas simulações',{exact:false}).waitFor();
    await shot('flow-context');await open();await page.getByRole('button',{name:'Capital e fluxo',exact:true}).click();
    assert.equal(await page.getByLabel('Delta normalizado mínimo',{exact:false}).inputValue(),'0.07');
    await page.setViewportSize({width:390,height:844});await shot('flow-mobile');
    const bounds=await page.getByRole('dialog').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=390);
    assert.deepEqual(errors,[]);console.log(JSON.stringify({flow_config_roundtrip:true,baseline_preserved:true,cards_and_shadow:true,mobile:true,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
