'use strict';
const assert=require('node:assert/strict');
const probe=require('./st100_readonly_probe.js');
function radio(name, checked=false, serviceId='') {
  return {checked,value:name,getAttribute:k=>k==='data-service-id'?serviceId:null,closest:()=>({textContent:name})};
}
function fakeDocument(withScope) {
  const scope=withScope?{querySelectorAll:()=>[radio('Яндекс Доставка',true,'y'),radio('СДЭК',false,'c'),radio('Ozon',false,'o')]}:null;
  return {querySelector:s=>s==='.t706'?{}:s==='.t706 form'?{}:s==='.t706 #customdelivery'?scope:null};
}
const win={tcart:{products:[{quantity:1}]},tcart_newDelivery:{updatePriceValueInRadio(){}},tcart__updateDelivery(){}};
const before=JSON.stringify(win.tcart);
const result=probe.inspect(fakeDocument(true),win);
assert.equal(result.advancedDeliveryFound,true);
assert.equal(result.deliveryOptions.length,3);
assert.equal(result.deliveryOptions[0].title,'Яндекс Доставка');
assert.equal(result.deliveryOptions[1].title,'СДЭК');
assert.equal(result.deliveryOptions[2].title,'Ozon');
assert.equal(result.priceUpdateFunctionAvailable,true);
assert.equal(result.recalcFunctionAvailable,true);
assert.equal(JSON.stringify(win.tcart),before,'Probe must not mutate cart');
const missing=probe.inspect(fakeDocument(false),{});
assert.equal(missing.advancedDeliveryFound,false);
assert.ok(missing.warnings.length>0);
console.log('PASS: ST100 read-only probe tests');
