# Ozon Delivery / Tilda ST100 — isolated development

## Implemented
- Python domain interface for listing pickup points, quoting and creating shipments.
- Demo provider with **synthetic** pickup points and clearly labeled sample quote.
- Local-only HTTP API: GET /api/status, GET /api/pvz?city=, POST /api/quote.
- Shipment creation disabled (501) and live provider fails closed.
- Unit tests for city filtering, validation, quote labeling and disabled writes.

## Run locally (from repository root)
```sh
python -m unittest integrations.ozon_delivery.test_delivery_core -v
python -m integrations.ozon_delivery.demo_api
curl 'http://127.0.0.1:8766/api/pvz?city=Москва'
curl -X POST http://127.0.0.1:8766/api/quote -H 'Content-Type: application/json' -d '{"point_id":"demo-msk-1","weight_grams":500}'
```

## NOT implemented
- Real Ozon pickup points, price, ETA, shipment, status, label, or cancellation APIs.
- OAuth access and refresh-token persistence.
- Tilda ST100 cart integration, checkout payment reconciliation, or live deployment.
- These examples are NOT valid for customer-facing checkout.

## Production prerequisites
1. Resolve OAuth and verify Ozon Logistics endpoint contracts with Ozon support.
2. Implement server-side auth, secret management, retries and idempotent shipment writes.
3. Obtain trusted cart weights, dimensions and delivery city; never trust browser-supplied price.
4. Test Tilda ST100 staging checkout; protect existing HEAD buy-to-cart script, Yandex Delivery and CDEK.
5. Add error fallback, user consent where applicable, logging without secrets, and full end-to-end tests.
6. Require explicit go-live review before enabling any production write or carrier selection.
