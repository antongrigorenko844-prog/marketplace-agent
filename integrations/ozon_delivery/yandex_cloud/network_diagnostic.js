'use strict';
// Isolated Yandex Cloud Functions connectivity diagnostic. No credentials, no orders.
// Run only via console test while function is private. No Tilda changes.
const dns=require('node:dns').promises;
const hosts=['api-seller-sandbox.ozon.ru','api.ozon.ru','example.com'];
module.exports.handler=async(event={})=>{
 const results=[];
 for(const host of hosts){
  const item={host};
  try {const a=await dns.lookup(host);item.dns='ok';item.family=a.family}
  catch(e){item.dns='failed';item.dnsCode=String(e.code||'unknown')}
  if(item.dns==='ok'){
   try{
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),2500);
    try{const response=await fetch('https://'+host+'/',{method:'GET',signal:controller.signal,redirect:'manual'});item.https='connected';item.httpStatus=response.status}
    finally{clearTimeout(timeout)}
   }catch(e){item.https='failed';item.errorName=e.name||'unknown';item.causeCode=String(e.cause?.code||'unknown')}
  }
  results.push(item);
 }
 return {statusCode:200,headers:{'Content-Type':'application/json'},body:JSON.stringify({diagnostic:true,results})};
};
