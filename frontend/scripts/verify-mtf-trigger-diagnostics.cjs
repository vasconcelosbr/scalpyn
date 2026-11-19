// Browser acceptance with synthetic, backend-generated diagnostics. No production calls.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const seed=JSON.parse(fs.readFileSync(process.env.MTF_SCHEMA_PATH,'utf8'));
const output=process.env.MTF_SCREENSHOT_DIR;fs.mkdirSync(output,{recursive:true});
const config={...seed.preset,poll_seconds:300,presentation:seed.presentation_preset};
let old=false,fail=false;const errors=[],writes=[];
function chart(symbol){return {symbol,analysis:{...seed.analysis,symbol,...(old?{trigger_diagnostics:undefined}:{})},profile:null,
  as_of:seed.analysis.observed_at_utc,indicator_config:{},frames:Object.fromEntries(Object.entries({'4h':14400,'1h':3600,'5m':300}).map(([tf,step])=>[tf,{
    candles:Array.from({length:60},(_,i)=>({time:1794990600-(59-i)*step,open:100,high:103,low:98,close:101+i*.001,volume:100,closed:true})),
    indicators:{},patterns:[],pivots:[],zones:[],source:'synthetic',stale:false}]))};}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.MTF_BROWSER_PATH?{executablePath:process.env.MTF_BROWSER_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1600,height:1000},locale:'pt-BR'});
 await context.addInitScript(()=>{localStorage.setItem('token','SYNTHETIC_LOCAL_TEST');localStorage.setItem('user',JSON.stringify({id:'fixture'}));});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const req=route.request(),u=new URL(req.url());let body={},status=200;
  if(req.method()!=='GET' && u.pathname!=='/api/mtf/preferences')writes.push(u.pathname);
  if(u.pathname==='/api/mtf/bootstrap')body={...seed,config,preferences:{timezone:'UTC',last_symbol:'TEST_USDT'},profiles:[],watchlists:[{id:'l3-consolidated',name:'L3 Consolidado'}]};
  else if(u.pathname==='/api/mtf/watchlist')body={items:['TEST_USDT','SECOND_USDT'].map(symbol=>({symbol,current_price:100,mtf:{...seed.analysis,symbol}})),limited:false};
  else if(u.pathname==='/api/mtf/chart'){
   if(fail){status=503;body={detail:'Synthetic failure'};}
   else {if(u.searchParams.get('symbol')==='SECOND_USDT')await new Promise(r=>setTimeout(r,350));body=chart(u.searchParams.get('symbol'));}
  }
  else if(u.pathname==='/api/mtf/history')body={items:[],next_cursor:null};
  else if(u.pathname==='/api/mtf/report')body={groups:[]};
  else if(u.pathname==='/api/auth/me')body={id:'fixture'};
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 try{
  await page.goto(process.env.MTF_BASE_URL || 'http://127.0.0.1:5001/grafico-mtf');
  const panel=page.getByRole('region',{name:'O que falta para o gatilho'});
  await panel.getByText('Padrão de compra no candle fechado',{exact:false}).waitFor();
  await panel.getByText('Aguardando condições anteriores',{exact:true}).click();
  assert((await panel.innerText()).includes('Aguardando padrão de gatilho.'));
  await panel.getByText('Aguardando condições anteriores',{exact:true}).click();
  await panel.getByText('Martelo / Pin de alta',{exact:true}).click();
  assert(await panel.getByText('Pavio inferior em relação ao corpo',{exact:false}).isVisible());
  assert(await panel.getByText('Abaixo do mínimo:',{exact:false}).count()>0);
  await panel.screenshot({path:path.join(output,'desktop.png')});
  await page.getByRole('button',{name:'Análise',exact:true}).click();
  await page.locator('.mtf-asset').filter({hasText:'SECOND/USDT'}).click();
  await panel.getByText('SECOND/USDT',{exact:true}).waitFor();
  await panel.getByText('Padrão de compra no candle fechado',{exact:false}).waitFor();
  assert(!(await panel.innerText()).includes('TEST/USDT'));
  for(const tf of ['1H','5M','4H'])await page.locator('.mtf-timeframes').getByRole('button',{name:tf,exact:true}).click();
  assert((await panel.innerText()).includes('Candle fechado:'));
  await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();
  const bounds=await panel.boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=390);
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  old=true;await page.getByRole('button',{name:'Atualizar',exact:true}).click();
  await panel.getByText('Detalhamento indisponível nesta observação.',{exact:true}).waitFor();
  old=false;fail=true;await page.getByRole('button',{name:'Atualizar',exact:true}).click();
  await panel.getByText('Avaliação indisponível ou desatualizada. Aguardando atualização.',{exact:true}).waitFor();
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({desktop:true,mobile:true,selection:true,timeframes:true,oldSnapshot:true,stale:true,writes,errors}));
 }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
