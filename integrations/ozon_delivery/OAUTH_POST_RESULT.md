# Ozon Sandbox OAuth: confirmed POST redirect (2026-11-17)

Private Yandex Cloud Functions test of documented POST /oauth/token with
sandbox client_credentials and redirect: manual returned:
- HTTP 307
- content-type text/html
- Location same host, query present
- No OAuth token response.

Earlier GET diagnostics also returned 307. This POST confirms redirect behavior
for the authorization request; **it does not identify the cause**. Possibilities
include gateway, anti-bot protection, or environment-specific routing. Do not
attempt bypass, follow arbitrary redirects with secrets, or log Location queries.

## Escalation
Ask Ozon developer support to verify whether Yandex Cloud egress traffic is
blocked/challenged and what approved authorization endpoint or configuration
is required. Supply UTC timestamp, HTTP status, source host, and any Ozon
request ID captured safely. Never send Client Secret, access tokens, nonce,
or full redirect Location with query.

## Deployment
Keep Yandex function PRIVATE. Disable/remove diagnostic after investigation.
Do not change Tilda, existing Buy button JS, Yandex Delivery or CDEK.
