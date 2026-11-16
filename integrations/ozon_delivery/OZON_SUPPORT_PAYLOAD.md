# Support-safe OAuth diagnostic payload

Method: POST https://api-seller-sandbox.ozon.ru/oauth/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json
Body (redacted, JSON representation only):
{"grant_type":"client_credentials","client_id":"[REDACTED]","client_secret":"[REDACTED]"}
Actual response was HTTP 307, content-type text/html; no JSON body.
Response metadata (JSON representation):
{"http_status":307,"content_type":"text/html","redirect_same_host":true,"redirect_has_query":true}
Full Location URL and query must NOT be sent without reviewing for tokens.
Approximate timestamp must come from Yandex Cloud logs or user's own test time; do not fabricate.
