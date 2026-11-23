/**
 * Read-only checkout amount reconciliation for ST100 staging.
 * Never modifies Tilda, never authorizes payment, never creates shipments.
 * Amounts are in integer kopecks; avoid float rounding and browser trust.
 */
(function(root){
'use strict';
function moneyKopecks(value){
 if(typeof value==='number'){if(!Number.isFinite(value)||value<0)return null;value=String(value);}
 if(typeof value!=='string'||!/^(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?$/.test(value))return null;
 const [whole,frac='']=value.split('.');
 const n=Number(whole)*100+Number((frac+'00').slice(0,2));
 return Number.isSafeInteger(n)?n:null;
}
function compare({goodsRub,shippingRub,paymentTotalRub,pointId,quotePointId}){
 const goods=moneyKopecks(goodsRub),shipping=moneyKopecks(shippingRub),total=moneyKopecks(paymentTotalRub);
 if([goods,shipping,total].some(v=>v===null))return {ok:false,reason:'invalid_amount'};
 if(!pointId||pointId!==quotePointId)return {ok:false,reason:'pickup_mismatch'};
 if(goods+shipping!==total)return {ok:false,reason:'payment_total_mismatch',expectedKopecks:goods+shipping,actualKopecks:total};
 return {ok:true,expectedKopecks:goods+shipping};
}
const api=Object.freeze({moneyKopecks,compare});
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.LeopartsPaymentIntegrity=api;
})(typeof window!=='undefined'?window:globalThis);
