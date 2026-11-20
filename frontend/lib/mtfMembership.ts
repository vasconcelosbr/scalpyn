import {ApiError} from './api';

/** A chart and a list are independent reads. Reconcile a chart 404 before declaring removal. */
export async function chartRemovalState(error:unknown,symbol:string,readMembers:()=>Promise<{items:{symbol:string}[]}>):Promise<'removed'|'pending'|'error'> {
  if(!(error instanceof ApiError)||error.status!==404||error.detail!=='ASSET_NO_LONGER_IN_WATCHLIST')return 'error';
  try {
    const fresh=await readMembers();
    return fresh.items.some(i=>i.symbol===symbol)?'pending':'removed';
  } catch {return 'pending';}
}
