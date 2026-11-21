'use strict';
// Private Cloud Functions console diagnostic: inspect redirects without credentials.
// No secret access, no order creation, no outbound POST.
const hosts=['https://api-seller-sandbox.ozon.ru/oauth/token','https://api.ozon.ru/oauth/token'];
module.exports.handler=async()=>{
 const results=[];
 for(const url of hosts){
  try{
   const c=new AbortController();const timer=setTimeout(()=>c.abort(),2500);
   try{
    const r=await fetch(url,{method:'GET',redirect:'manual',signal:c.signal});
    const location=r.headers.get('location');
    const target=location?new URL(location,url):null;
    results.push({host:new URL(url).host,path:new URL(url).pathname,status:r.status,
      redirectHost:target?.host||null,redirectPath:target?.pathname||null,
      contentType:r.headers.get('content-type')||null});
   }finally{clearTimeout(timer)}
  }catch(e){results.push({host:new URL(url).host,error:e.name||'error',causeCode:e.cause?.code||null})}
 }
 return {statusCode:200,headers:{'Content-Type':'application/json; charset=utf-8'},
 body:JSON.stringify({diagnostic:'ozon_redirects_no_credentials',results})};
};
