'use strict';
const assert=require('node:assert/strict');
const {mayUpdatePrice}=require('./st100_adapter_guard.js');
const radios=(selected)=>['Яндекс Доставка','СДЭК','Ozon'].map(title=>({title,checked:title===selected}));
const base={pointId:'pvz-1',quote:{pointId:'pvz-1',priceRub:200},cartFingerprint:'cart1',quoteFingerprint:'cart1'};
for(const name of ['Яндекс Доставка','СДЭК']){
 assert.equal(mayUpdatePrice({...base,radios:radios(name)}).allowed,false,name+' must be isolated');
}
assert.equal(mayUpdatePrice({...base,radios:radios('Ozon')}).allowed,true);
assert.equal(mayUpdatePrice({...base,radios:[{title:'Ozon, пункт выдачи',checked:true}]}).allowed,true);
assert.equal(mayUpdatePrice({...base,radios:[{title:'Озон — ПВЗ',checked:true}]}).allowed,true);
assert.equal(mayUpdatePrice({...base,radios:[{title:'Не Ozon',checked:true}]}).allowed,false);
assert.equal(mayUpdatePrice({...base,radios:[{title:'OzonExpress',checked:true}]}).allowed,false);
assert.equal(mayUpdatePrice({...base,radios:radios('Ozon'),quoteFingerprint:'old'}).reason,'stale_cart');
assert.equal(mayUpdatePrice({...base,radios:radios('Ozon'),quote:{pointId:'other',priceRub:200}}).reason,'quote_point_mismatch');
assert.equal(mayUpdatePrice({...base,radios:radios('Ozon'),quote:{pointId:'pvz-1',priceRub:-1}}).reason,'invalid_price');
assert.equal(mayUpdatePrice({...base,radios:radios('Ozon'),pointId:''}).reason,'missing_point');
console.log('PASS: ST100 Ozon-only write policy');
