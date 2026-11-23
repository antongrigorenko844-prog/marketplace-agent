/**
 * Pure, framework-free quote state machine for a future Ozon checkout adapter.
 * Does not read/write DOM, Tilda cart, delivery services, or external networks.
 * Prices from the browser must never be treated as authoritative by the backend.
 */
(function(root){
'use strict';
function create(){
 let state={carrier:null,pointId:null,cartFingerprint:null,requestId:0,quote:null};
 function snapshot(){return {...state,quote:state.quote?{...state.quote}:null};}
 function reset(){state={carrier:null,pointId:null,cartFingerprint:null,requestId:state.requestId+1,quote:null};return snapshot();}
 return Object.freeze({
  snapshot,
  chooseCarrier(carrier){if(carrier!=='ozon')return reset();if(state.carrier!=='ozon'){state.carrier='ozon';state.pointId=null;state.quote=null;state.cartFingerprint=null;state.requestId++;}return snapshot();},
  choosePoint(pointId){if(state.carrier!=='ozon'||typeof pointId!=='string'||!pointId.trim())throw Error('Invalid Ozon point');if(pointId!==state.pointId){state.pointId=pointId;state.quote=null;state.cartFingerprint=null;state.requestId++;}return snapshot();},
  beginQuote(fingerprint){if(state.carrier!=='ozon'||!state.pointId||typeof fingerprint!=='string'||!fingerprint)throw Error('Quote prerequisites missing');state.cartFingerprint=fingerprint;state.quote=null;state.requestId++;return Object.freeze({requestId:state.requestId,pointId:state.pointId,cartFingerprint:fingerprint});},
  receiveQuote(ticket,result){if(!ticket||ticket.requestId!==state.requestId||ticket.pointId!==state.pointId||ticket.cartFingerprint!==state.cartFingerprint||state.carrier!=='ozon')return false;
    if(!result||result.available!==true||!Number.isSafeInteger(result.priceRub)||result.priceRub<0){state.quote=null;return false;}
    state.quote={priceRub:result.priceRub,source:result.source||'unknown',verified:false};return true;},
  cartChanged(fingerprint){if(fingerprint!==state.cartFingerprint){state.cartFingerprint=null;state.quote=null;state.requestId++;}return snapshot();},
  canProceed(){return false;} // Never authorize production checkout from client-side state.
 });
}
const api=Object.freeze({create});
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.LeopartsOzonQuoteState=api;
})(typeof window!=='undefined'?window:globalThis);
