/**
 * ST100 adapter guard: only permits writes while Ozon is selected.
 * Staging-only pure policy module; no DOM writes or checkout calls.
 */
(function(root){
'use strict';
function isOzonSelected(radios){
 const chosen=Array.from(radios||[]).find(r=>r.checked);
 if(!chosen)return false;
 const title=String(chosen.title||chosen.label||'');
 return /(?:^|\s)(?:ozon|озон)(?:\s|$)/i.test(title);
}
function mayUpdatePrice({radios,pointId,quote,cartFingerprint,quoteFingerprint}){
 if(!isOzonSelected(radios))return {allowed:false,reason:'other_carrier'};
 if(typeof pointId!=='string'||!pointId)return {allowed:false,reason:'missing_point'};
 if(!quote||quote.pointId!==pointId)return {allowed:false,reason:'quote_point_mismatch'};
 if(typeof cartFingerprint!=='string'||!cartFingerprint||cartFingerprint!==quoteFingerprint)return {allowed:false,reason:'stale_cart'};
 if(!Number.isSafeInteger(quote.priceRub)||quote.priceRub<0)return {allowed:false,reason:'invalid_price'};
 return {allowed:true,reason:'ozon_only'};
}
const api=Object.freeze({isOzonSelected,mayUpdatePrice});
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.LeopartsOzonAdapterGuard=api;
})(typeof window!=='undefined'?window:globalThis);
