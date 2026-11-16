'use strict';
// Diagnostic: never print URL, query values, credentials, tokens or cookies.
const crypto=require('node:crypto');
const json=(body)=>({statusCode:200,headers:{'Content-Type':'application/json','Cache-Control':'no-store'},body:JSON.stringify(body)});
module.exports.handler=async(event={})=>{
 if(event.path!=='/internal/redirect-details')return {statusCode:404,body:'Not found'};
 const nonce=process.env.OZON_TEST_NONCE;
 if(!nonce||typeof event.nonce!=='string'||!crypto.timingSafeEqual(crypto.createHash('sha256').update(nonce).digest(),crypto.createHash('sha256').update(event.nonce).digest()))return {statusCode:404,body:'Not found'};
 const client_id=process.env.OZON_CLIENT_ID,client_secret=process.env.OZON_SANDBOX_CLIENT_SECRET;
 if(!client_id||!client_secret)return json({error:'missing_credentials'});
 const ac=new AbortController();const timeout=setTimeout(()=>ac.abort(),8000);
 try{
 const r=await fetch('https://api-seller-sandbox.ozon.ru/oauth/token',{method:'POST',redirect:'manual',signal:ac.signal,headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({grant_type:'client_credentials',client_id,client_secret})});
 const loc=r.headers.get('location');
 let redirect={present:!!loc};
 if(loc){const u=new URL(loc,'https://api-seller-sandbox.ozon.ru');
 redirect={present:true,sameHost:u.hostname==='api-seller-sandbox.ozon.ru',pathIsOauthToken:u.pathname==='/oauth/token',queryParameterNames:[...new Set(u.searchParams.keys())].filter(k=>/^[a-zA-Z0-9_-]{1,64}$/.test(k)).slice(0,20),queryParameterCount:[...u.searchParams.keys()].length};
 }
 return json({diagnostic:'redirect_metadata_only',httpStatus:r.status,contentType:(r.headers.get('content-type')||'').split(';')[0],redirect,serverHeaderPresent:!!r.headers.get('server'),setCookiePresent:!!r.headers.get('set-cookie'),retryAfterPresent:!!r.headers.get('retry-after')});
 }catch(e){return json({error:e.name||'request_failed'})}finally{clearTimeout(timeout)}
};
