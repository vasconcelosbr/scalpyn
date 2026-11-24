'use client';
import {apiFetch, ApiError} from '@/lib/api';
import type {Bootstrap} from './types';

export function MTFJsonTools({text,onText,catalog,disabled,setBusy,onMessage,onErrors}:{
 text:string;onText:(text:string)=>void;catalog:Bootstrap['catalog'];disabled:boolean;
 setBusy:(busy:boolean)=>void;onMessage:(message:string)=>void;onErrors:(errors:Record<string,string>)=>void;
}) {
 const parsed=()=>{const value=JSON.parse(text);if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Objeto JSON obrigatório.');return value;};
 const error=(e:unknown)=>{
  if(e instanceof ApiError && e.rawBody){try{const detail=JSON.parse(e.rawBody).detail;if(Array.isArray(detail))onErrors(Object.fromEntries(detail.map(x=>[x.loc.filter((p:unknown)=>p!=='body').join('.'),x.msg])));}catch{/* Original error below. */}}
  onMessage(e instanceof Error?e.message:'JSON inválido.');
 };
 const validate=async()=>{try{setBusy(true);onErrors({});await apiFetch('/mtf/config/validate',{method:'POST',body:JSON.stringify(parsed())});onMessage('JSON válido. Nenhuma alteração foi salva.');}catch(e){error(e);}finally{setBusy(false);}};
 const download=()=>{try{const url=URL.createObjectURL(new Blob([JSON.stringify(parsed(),null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='mtf-config.json';link.click();URL.revokeObjectURL(url);}catch(e){error(e);}};
 return <>
  <div className="mtf-json-tools">
   <button type="button" disabled={disabled} onClick={validate}>Validar sem salvar</button>
   <button type="button" disabled={disabled} onClick={()=>{try{onText(JSON.stringify(parsed(),null,2));}catch(e){error(e);}}}>Formatar</button>
   <button type="button" disabled={disabled} onClick={download}>Exportar JSON</button>
   <label>Importar JSON<input type="file" accept="application/json,.json" disabled={disabled} onChange={async e=>{const file=e.target.files?.[0];if(file){try{const value=JSON.parse(await file.text());if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Objeto JSON obrigatório.');onText(JSON.stringify(value,null,2));onMessage('Arquivo carregado. Valide e salve para aplicar.');}catch(failure){error(failure);}}}}/></label>
  </div>
  {catalog && <details className="mtf-json-catalog"><summary>Catálogo · indicadores, parâmetros e condições</summary><p>Use indicators.ID para referenciar uma instância. Cada instância define parâmetros, saída, timeframe e janela de cálculo. Grupos usam logic AND/OR e conditions. Dados ausentes não aprovam regras.</p><pre>{JSON.stringify(catalog,null,2)}</pre></details>}
 </>;
}
