# Tilda ST100 — safe staging validation plan

**DO NOT EDIT production Tilda HEAD script, buy-to-cart behavior, Yandex Delivery, CDEK, or vk-proxy.**

## Boundaries
- Ozon selection must be isolated from existing delivery options.
- The buyer cannot place an Ozon order from the demo.
- Sample prices and delivery windows must never enter a real order or payment total.
- Only server-verified Ozon quote may eventually be used for a real checkout.
- Browser-provided point ID, address, weight and prices are untrusted.
- No OAuth secrets or access tokens in HTML or Tilda scripts.

## ST100 compatibility gates
1. Clone the relevant page/site to a non-public staging environment.
2. Document current cart, checkout, payment and delivery field mapping.
3. Verify whether Tilda ST100 can accept a third custom shipping provider and its server-verified price. Do not assume it can.
4. Check what is saved in order notifications, CRM, payment amount and shipping metadata.
5. Test city changes, point changes, missing weights, cancellations, retries and payment failures.
6. Test Yandex Delivery and CDEK unchanged, including checkout totals.
7. Obtain verified Ozon logistics endpoints, OAuth and shipping credentials before any live testing.
8. Deploy only after rollback and explicit go-live approval.

## Current result
Domain-layer contract and unit tests exist. No ST100 integration, live quote or production checkout has been verified.
