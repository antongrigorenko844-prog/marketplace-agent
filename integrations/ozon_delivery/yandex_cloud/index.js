'use strict';
// Yandex Cloud Functions Node.js 22.
// Safe OAuth callback skeleton. Does NOT exchange codes or create shipments.
// Keep this endpoint PRIVATE until OAuth flow, state persistence and token storage are validated.
const crypto = require('node:crypto');
const json = (statusCode, value) => ({
  statusCode,
  headers: {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'},
  body: JSON.stringify(value)
});
module.exports.handler = async function(event = {}) {
  const path = event.path || '/';
  if (path === '/' || path === '/health') {
    return json(200, {
      service:'leoparts-ozon-delivery',
      status:'ok',
      mode:'test',
      ozonConnected:false,
      secretVariablesPresent: Boolean(process.env.OZON_CLIENT_ID && process.env.OZON_CLIENT_SECRET)
    });
  }
  if (path === '/oauth/callback') {
    // Never log or echo query parameters: may include authorization code.
    // No OAuth token exchange until Ozon's exact documented flow is verified.
    return json(503, {status:'not_configured',message:'OAuth callback disabled pending verified Ozon documentation'});
  }
  return json(404,{error:'Not found'});
};
