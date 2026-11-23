'use strict';
// Inspect 307 redirect targets WITHOUT sending credentials or following redirects.
// Intended for private Yandex Cloud Functions console testing only.
const urls=['https://api-seller-sandbox.ozon.ru/oauth/token','https://api.ozon.ru/oauth/token'];
module.exports.handler=async()=>{
 const results=[];
 for(const url of urls){
  try{
   const c=new AbortController(), t=setTimeout(()=>c.abort(),4000);
   try{
    const r=await fetch(url,{method:'GET',redirect:'manual',signal:c.signal});
    const raw=r.headers.get('location');
    const target=raw?new URL(raw,url):null;
    results.push({
      source:new URL(url).origin,
      status:r.status,
      redirectProtocol:target?.protocol||null,
      redirectHost:target?.host||null,
      redirectPath:target?.pathname||null,
      redirectHasQuery:!!target?.search,
      redirectsToSameUrl:target?target.origin+target.pathname===new URL(url).origin+new URL(url).pathname:false
    });
   }finally{clearTimeout(t)}
  }catch(e){results.push({source:new URL(url).origin,error:e.name||'error',cause:e.cause?.code||null})}
 }
 return {statusCode:200,headers:{'Content-Type':'application/json'},body:JSON.stringify({results})};
};
