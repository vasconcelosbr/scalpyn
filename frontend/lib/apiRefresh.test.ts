import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apiFetch } from './api';

test('refresh is single-flight, bounded and never resurrects a logged-out session',async()=>{
  const originalFetch=globalThis.fetch;
  const store=new Map([['token','old'],['refresh_token','refresh']]);
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:(k:string)=>store.get(k)??null,setItem:(k:string,v:string)=>store.set(k,v),removeItem:(k:string)=>store.delete(k)}});
  Object.defineProperty(globalThis,'window',{configurable:true,value:{location:{href:''}}});
  let refreshCalls=0;
  globalThis.fetch=async(input,init)=>{
    if(String(input).endsWith('/auth/refresh')){refreshCalls++;await new Promise(r=>setTimeout(r,10));return Response.json({access_token:'new'});}
    return (init?.headers as Record<string,string>).Authorization==='Bearer new'?Response.json({ok:true}):new Response('',{status:401});
  };
  try {
    const results=await Promise.all([apiFetch('/mtf/chart'),apiFetch('/mtf/watchlist')]);
    assert.deepEqual(results,[{ok:true},{ok:true}]);assert.equal(refreshCalls,1);
    store.set('token','old');
    globalThis.fetch=async(input)=>String(input).endsWith('/auth/refresh')?new Response('',{status:503}):new Response('',{status:401});
    await assert.rejects(()=>apiFetch('/mtf/chart'),/503/);assert.equal(store.get('token'),'old');
    globalThis.fetch=async(input)=>{
      if(String(input).endsWith('/auth/refresh')){store.clear();return Response.json({access_token:'resurrected'});}
      return new Response('',{status:401});
    };
    await assert.rejects(()=>apiFetch('/mtf/chart'));assert.equal(store.get('token'),undefined);
    store.set('token','old');store.set('refresh_token','refresh');
    let attempts=0;
    globalThis.fetch=async(input)=>{
      if(String(input).endsWith('/auth/refresh')){attempts++;return Response.json({access_token:'still-invalid'});}
      return new Response('',{status:401});
    };
    await assert.rejects(()=>apiFetch('/mtf/chart'),/401/);
    assert.equal(attempts,1);assert.equal(store.get('refresh_token'),undefined);
    store.set('token','old');store.set('refresh_token','refresh');
    globalThis.fetch=async(input,init)=>{
      if(String(input).endsWith('/auth/refresh'))return Response.json({access_token:'new'});
      if((init?.headers as Record<string,string>).Authorization==='Bearer new'){
        store.set('token','other-user');store.set('refresh_token','other-refresh');
      }
      return new Response('',{status:401});
    };
    await assert.rejects(()=>apiFetch('/mtf/chart'),/409/);
    assert.equal(store.get('token'),'other-user');
  } finally {globalThis.fetch=originalFetch;Reflect.deleteProperty(globalThis,'window');Reflect.deleteProperty(globalThis,'localStorage');}
});
