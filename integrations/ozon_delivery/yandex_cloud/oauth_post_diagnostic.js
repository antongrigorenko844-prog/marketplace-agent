'use strict';
// ONE-TIME private-console-only OAuth POST diagnostic. Do not deploy publicly.
// Does not return credentials, token, response body, redirect query or headers.
const crypto=require('node:crypto');
const json=(statusCode,body)=>({statusCode,headers:{'Content-Type':'application/json','Cache-Control':'no-store'},body:JSON.stringify(body)});
module.exports.handler=async(event={})=>{
  if(event.path!=='/internal/sandbox-auth') return json(404,{error:'Not found'});
  const expected=process.env.OZON_TEST_NONCE;
  const supplied=event.nonce;
  if(!expected || typeof supplied!=='string' ||
     !crypto.timingSafeEqual(crypto.createHash('sha256').update(expected).digest(),
                             crypto.createHash('sha256').update(supplied).digest()))
    return json(404,{error:'Not found'});
  const id=process.env.OZON_CLIENT_ID,secret=process.env.OZON_SANDBOX_CLIENT_SECRET;
  if(!id||!secret) return json(503,{status:'missing_sandbox_credentials'});
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),8000);
  try{
    const response=await fetch('https://api-seller-sandbox.ozon.ru/oauth/token',{
      method:'POST',redirect:'manual',signal:controller.signal,
      headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},
      body:new URLSearchParams({grant_type:'client_credentials',client_id:id,client_secret:secret})
    });
    // Never parse/print token or raw redirect Location; query may contain sensitive data.
    const loc=response.headers.get('location');
    let redirectSameHost=null,redirectHasQuery=null;
    if(loc){
      const u=new URL(loc,'https://api-seller-sandbox.ozon.ru');
      redirectSameHost=u.hostname==='api-seller-sandbox.ozon.ru';
      redirectHasQuery=!!u.search;
    }
    return json(200,{
      diagnostic:'oauth_post_manual_redirect',httpStatus:response.status,
      contentType:(response.headers.get('content-type')||'').split(';')[0],
      redirectSameHost,redirectHasQuery,
      note:'No response body or token inspected; 200 alone does not confirm authorization'
    });
  }catch(err){
    return json(502,{diagnostic:'oauth_post_manual_redirect',errorName:err.name||'Error',causeCode:err.cause?.code||null});
  }finally{clearTimeout(timer)}
};
