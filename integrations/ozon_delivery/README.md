# Ozon Delivery × Tilda ST100 — isolated prototype

Status: **design only, not deployed**. Never edit existing Tilda HEAD script, which opens the ST100 cart on Buy clicks.

## Current constraints
- Tilda ST100, advanced delivery mode enabled.
- Yandex Delivery and CDEK are live; preserve both.
- Ozon Delivery connected for own store in Ozon Seller (FBO/FBS), but **API credentials/permissions have not been verified**.
- No paid third-party modules.
- No modification of production Tilda checkout, payment, or forms without staging validation.

## Intended customer flow
1. Existing Buy click opens ST100, unchanged.
2. Buyer chooses Ozon Delivery alongside existing shipping options if integration is feasible; otherwise show an isolated, explicitly labeled Ozon selector without replacing native choices.
3. Select city, see live pickup points, choose one.
4. Show confirmed quote and ETA; retain point ID, address, quote ID and order/cart identity.
5. Checkout submits selected pickup point with the order; server verifies price and order before creating a shipment.
6. On API error, block Ozon checkout with an explanatory message; never silently fall back to another carrier.

## Technical design (pending verification)
- Frontend widget in **separate JS file**, loaded only on a test page.
- Backend HTTPS adapter (hosting TBD): endpoints for city/PVZ lookup, quote, and shipment creation, subject to actual documented Ozon API capabilities.
- Ozon credentials remain server-side; never put tokens in Tilda HEAD or public repository.
- Verify Tilda ST100 advanced-delivery lifecycle and supported custom fields before writing DOM integration. Do not mutate undocumented internal cart state on production.
- Protect against double shipment creation (idempotency), validate order totals server-side, handle CORS, rate limits, and API errors.
- Do not assume Ozon Seller's connected-app status grants Delivery API access.

## Acceptance checks before any production changes
- Existing Buy buttons still open ST100.
- Yandex and CDEK options, PVZ maps, calculations and checkout unchanged.
- Ozon PVZ data and quote verified against real API sandbox/test responses.
- Selected Ozon PVZ saved in Tilda order; paid orders create shipment once.
- Mobile and desktop tested; Ozon failure doesn't break other carriers.
- Rollback: remove only new widget script; existing HEAD remains untouched.

## Next dependencies
1. Confirm official Ozon Delivery API access and authentication for the own-store integration (without sharing credentials).
2. Inspect a safe copy of the published ST100 checkout and determine whether its advanced shipping UI can be extended reliably.
3. Choose backend deployment location; GitHub Actions alone cannot serve an interactive API.
