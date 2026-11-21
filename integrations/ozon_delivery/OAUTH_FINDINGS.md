# OAuth investigation — 2026-11-17

## Verified
Official Ozon Sandbox guide (2026-10-19):
https://dev.ozon.ru/start/562-Sandbox-v-Seller-API-novye-vozmozhnosti-dlia-integratsii/

POST https://api-seller-sandbox.ozon.ru/oauth/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json
client_id, client_secret (sandbox), grant_type=client_credentials.

The earlier sandbox_auth.js already uses these documented parameters. It returned
502 ozon_unreachable/request_failed on Yandex Cloud Functions. The subsequent
GET (not POST) diagnostic requests to /oauth/token returned 307 with Location
on the same host/path and a query string. That does NOT prove the POST has the
same redirect behavior, nor prove incorrect credentials.

Relevant official Ozon developer community reports of OAuth antibot behavior:
https://dev.ozon.ru/community/2311-OAuth-oauth-token-blokiruetsia-antibotom/
https://dev.ozon.ru/community/2170-Otkaz-v-dostupe-pri-poluchenii-Oauth-tokena/

## Next diagnostic
Test POST with redirect:'manual' and redact all Location/query/body/token.
Log only HTTP status, content type, and whether ozon-antibot header exists.
If 307/403/antibot appears, contact Ozon support with timestamp, incident ID
(if available), and cloud egress IP; do not attempt bypassing protections.
No repeated automated probing.

## Deployment constraints
Do not deploy diagnostics to public endpoint. Existing Tilda HEAD Buy-button
script, native Yandex/CDEK shipping and vk-proxy are out of scope.
