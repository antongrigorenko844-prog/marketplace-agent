'use strict';
// Ozon Sandbox OAuth test — JSON request per Ozon API schema.
// Private Yandex Cloud Functions console only. No token or secret in response/logs.
const crypto=require('node:crypto');
const reply=(statusCode,body)=>({statusCode,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'},body:JSON.stringify(body)});
module.exports.handler=async(event={})=>{
 if(event.path==='/health'||event.path==='/') return reply(200,{status:'ok',mode:'test',ozonConnected:false,
  clientIdConfigured:!!process.env.OZON_CLIENT_ID,
  sandboxSecretConfigured:!!process.env.OZON_SANDBOX_CLIENT_SECRET});
 if(event.path!=='/internal/sandbox-auth') return reply(404,{error:'Not found'});
 const expected=process.env.OZON_TEST_NONCE, supplied=event.nonce;
 if(!expected||typeof supplied!=='string'||!crypto.timingSafeEqual(
  crypto.createHash('sha256').update(expected).digest(),
  crypto.createHash('sha256').update(supplied).digest())) return reply(404,{error:'Not found'});
 const client_id=process.env.OZON_CLIENT_ID,client_secret=process.env.OZON_SANDBOX_CLIENT_SECRET;
 if(!client_id||!client_secret) return reply(503,{error:'missing_credentials'});
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
 try{
  const r=await fetch('https://api-seller-sandbox.ozon.ru/oauth/token',{
   method:'POST',redirect:'manual',signal:controller.signal,
   headers:{'Content-Type':'application/json','Accept':'application/json'},
   body:JSON.stringify({client_id,client_secret,grant_type:'client_credentials'})
  });
  const result={httpStatus:r.status,contentType:(r.headers.get('content-type')||'').split(';')[0]};
  if(r.status>=300&&r.status<400){
   const location=r.headers.get('location');
   result.redirect=true;
   if(location){const u=new URL(location,'https://api-seller-sandbox.ozon.ru');
    result.redirectSameHost=u.hostname==='api-seller-sandbox.ozon.ru';
    result.redirectHasQuery=!!u.search;
   }
  }else if(r.ok&&result.contentType==='application/json'){
   const data=await r.json();
   result.tokenReceived=typeof data.access_token==='string'&&data.access_token.length>0;
   result.authenticated=result.tokenReceived;
   // Deliberately discard token; do not persist or return it in this diagnostic.
  }else result.authenticated=false;
  return reply(200,{diagnostic:'sandbox_oauth_json',...result});
 }catch(e){return reply(502,{diagnostic:'sandbox_oauth_json',errorName:e.name||'Error',causeCode:e.cause?.code||null})}
 finally{clearTimeout(timer)}
};
