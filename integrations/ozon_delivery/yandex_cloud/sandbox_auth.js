'use strict';
// Ozon Delivery OAuth helper for Yandex Cloud Functions, Node.js 22.
// Sandbox ONLY. Never return token or credentials. Never create live orders.
// The sandbox requires a DIFFERENT secret: OZON_SANDBOX_CLIENT_SECRET.
const json = (statusCode, body) => ({
  statusCode,
  headers: {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'},
  body: JSON.stringify(body)
});
const timeoutMs = 8000;
module.exports.handler = async function(event = {}) {
  const path = event.path || '/';
  if (path === '/' || path === '/health') {
    return json(200, {
      service:'leoparts-ozon-delivery', status:'ok', mode:'test',
      ozonConnected:false,
      clientIdConfigured:Boolean(process.env.OZON_CLIENT_ID),
      productionSecretConfigured:Boolean(process.env.OZON_CLIENT_SECRET),
      sandboxSecretConfigured:Boolean(process.env.OZON_SANDBOX_CLIENT_SECRET)
    });
  }
  if (path === '/oauth/callback') {
    return json(503, {error:'OAuth production callback disabled until persistent state and token storage implemented'});
  }
  // Do not expose an external endpoint that triggers token requests.
  // Cloud Functions console testing must invoke with path /internal/sandbox-auth
  // AND secret nonce; not intended for public access.
  if (path === '/internal/sandbox-auth') {
    const expected = process.env.OZON_TEST_NONCE;
    const provided = event?.queryStringParameters?.nonce || event?.nonce;
    if (!expected || typeof provided !== 'string' ||
        !require('node:crypto').timingSafeEqual(
          Buffer.from(require('node:crypto').createHash('sha256').update(provided).digest()),
          Buffer.from(require('node:crypto').createHash('sha256').update(expected).digest())
        )) return json(404,{error:'Not found'});
    const id=process.env.OZON_CLIENT_ID;
    const secret=process.env.OZON_SANDBOX_CLIENT_SECRET;
    if (!id || !secret) return json(503,{status:'not_configured',reason:'sandbox credentials missing'});
    try {
      const controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),timeoutMs);
      let response;
      try {
        response=await fetch('https://api-seller-sandbox.ozon.ru/oauth/token',{
          method:'POST',
          headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},
          body:new URLSearchParams({grant_type:'client_credentials',client_id:id,client_secret:secret}),
          signal:controller.signal
        });
      } finally {clearTimeout(timeout)}
      if (!response.ok) return json(502,{status:'ozon_auth_failed',httpStatus:response.status});
      const payload=await response.json();
      return json(200,{status:payload.access_token?'sandbox_authenticated':'unexpected_response',tokenReceived:Boolean(payload.access_token)});
    } catch(e) {return json(502,{status:'ozon_unreachable',reason:e?.name==='AbortError'?'timeout':'request_failed'})}
  }
  return json(404,{error:'Not found'});
};
