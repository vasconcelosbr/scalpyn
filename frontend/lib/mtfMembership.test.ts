import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiError} from './api';
import {chartRemovalState} from './mtfMembership';

const removed=new ApiError({status:404,detail:'ASSET_NO_LONGER_IN_WATCHLIST',endpoint:'/mtf/chart',path:'/api/mtf/chart',method:'GET',rawBody:null});
test('racing chart 404 cannot declare removal while fresh watchlist contains the asset',async()=>{
  assert.equal(await chartRemovalState(removed,'EVAA_USDT',async()=>({items:[{symbol:'EVAA_USDT'}]})),'pending');
  assert.equal(await chartRemovalState(removed,'EVAA_USDT',async()=>({items:[]})),'removed');
  assert.equal(await chartRemovalState(removed,'EVAA_USDT',async()=>{throw Error('offline');}),'pending');
  assert.equal(await chartRemovalState(Error('offline'),'EVAA_USDT',async()=>({items:[]})),'error');
});
