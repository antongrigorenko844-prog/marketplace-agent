# ADR-001 — Ozon Delivery for Leoparts / Tilda ST100 (2026-11-17)

## Decision: STOP building an unverified checkout integration

**Recommended direction:** retain existing Tilda ST100 checkout for Yandex/CDEK. First validate Ozon's *seller-api.ozon-logistics* production flow in an isolated backend, then run a non-public ST100/payment feasibility test. Do **not** install demo widget or alter live checkout. Do **not** migrate to a different Ozon API merely because Sandbox OAuth returns 307.

## Verified from official documentation

Ozon: https://dev.ozon.ru/start/446-Ozon-Logistika-podkliuchenie-nastroika-i-poriadok-raboty-s-novym-funktsionalom/

- The *seller-api.ozon-logistics* permission explicitly serves deliveries from a seller's own website. This matches the existing app scope.
- Documented sequence: /v1/delivery/check (buyer phone) -> /v1/delivery/map or /v1/delivery/point/list -> /v2/delivery/checkout (stock/route/ETA/splits) -> **buyer payment confirmed** -> /v2/order/create -> follow-up status/cancellation.
- /v1/delivery/map does **not** check delivery feasibility or ETA.
- Ozon requires registered Ozon stock for order creation.
- The guide explicitly says Ozon **does not provide a sandbox environment for testing this seller delivery functionality**; internal authorized phone numbers and careful cancellation are suggested.
- The checkout response's precise **customer shipping charge** must be verified: the guide mentions calculated commissions and dates, which is not proof that it supplies a customer-facing shipping price.
- Ozon's order creation is not guaranteed by HTTP 200 alone.

Tilda: https://help-ru.tilda.cc/online-store/shipping
- ST100 simple delivery options have configured fixed prices.
- Advanced mode uses only delivery services registered in site settings.
- Custom pickup-point option is manually configured by city, address, cost and time; this is not an automatic Ozon API adapter.
- No documented third-party dynamic tariff injection / trusted payment-total override was found in the reviewed shipping docs. **Absence of documentation is not proof that no supported integration route exists.**

## Critical correction

Sandbox OAuth 307 to /oauth/token?__rr=... remains unresolved. It is not proof that the seller delivery API is unusable, and repeatedly rotating credentials is not productive. The Ozon guide says the *logistics functionality itself* lacks a sandbox. Confirm with Ozon support which production OAuth flow and endpoints apply to this private app before any production test.

## Current reusable assets
- Yandex Cloud function, Lockbox, private Ozon app; retain without redeploying.
- GitHub demo selection and isolated Python contracts; **not production-ready**, no live API, no Tilda connection.
- Existing Yandex/CDEK and HEAD buy-to-cart script are out of scope and must remain unchanged.

## Blocking questions, in priority order

1. **Pricing:** Does /v2/delivery/checkout return a buyer-payable shipping amount or only seller commission? If only commission, determine who sets buyer-facing delivery charge and how.
2. **Payment:** Can Tilda ST100 add a third custom carrier with server-verified dynamic amount and pickup-point ID while preserving the current Yandex/CDEK options and payment reconciliation? Request a concrete supported mechanism from Tilda.
3. **Identity/stock:** How are Tilda SKUs matched to Ozon stock and FBO/FBS availability? How does buyer phone verification via /v1/delivery/check work?
4. **Production auth:** Which endpoint, redirect flow and credential pair are valid for this app? Resolve 307 separately.
5. **Operational safety:** Payment confirmation -> idempotent order create, partial fulfillment, status reconciliation, cancellations, refunds, returns, logging.

## Architecture alternatives (do not implement until gates are answered)

A. **ST100 native custom delivery:** best if Tilda confirms dynamic quote/payment integration and secure storage of pickup ID. Lowest change risk.
B. **ST100 fixed-price Ozon shipping:** possible only if customer-facing pricing, legal terms, regional restrictions and payment/fulfillment are validated. Not equivalent to real-time Ozon rates.
C. **Isolated Ozon checkout/payment flow:** technically flexible but higher scope, fiscalization, payment reconciliation, fraud and customer support costs. Consider only if A/B fail.

## Go/no-go

- **No-go now** for production Tilda edits, real customer quotes, or shipment creation.
- **Go** for document/API contract analysis, isolated tests, and direct written confirmation from Ozon/Tilda.
- User should not have to perform any new configuration until a specific missing external fact makes it unavoidable.

## Questions to send Tilda support

We use ST100 advanced shipping with Yandex Delivery and CDEK already enabled. Can a custom third carrier use an external API to calculate a **server-verified** shipping amount and pickup-point ID that become part of the **actual online payment total and submitted order**? Please identify the documented integration mechanism or confirm that it is unsupported. We must retain the existing two services and current add-to-cart behavior.

## Questions to send Ozon support

For private app with seller-api.ozon-logistics, does /v2/delivery/checkout provide **customer-payable shipping price** or only seller logistics commissions? Does the private app use production OAuth Authorization Code Flow, and is Sandbox OAuth a meaningful test for this seller-delivery functionality? Please point to the exact endpoint schema.

## 2026-11-17 verified update — customer-visible shipping price

Ozon official announcement dated **2026-08-06** explicitly states that `v2/delivery/checkout` now returns preliminary delivery cost and that merchants can **display the current delivery cost to customers at checkout and dynamically update it as cart items change**:
https://dev.ozon.ru/news/780-Ozon-Dostavka-dlia-prodavtsov-podderzhka-rascheta-predvaritelnoi-stoimosti-dostavki/

This resolves the prior question of whether the API exposes a displayable preliminary shipping cost **at the product capability level**. The exact response field, pricing policy, final charged amount, and how Tilda ST100 collects that amount remain to be verified. Do not conflate estimated logistics cost with an authorized final payment amount.

Tilda's official shipping docs document fixed-price variants and manually maintained pickup points; they do not establish a supported mechanism for arbitrary third-party API prices to be included in ST100 payment total: https://help-ru.tilda.cc/online-store/shipping

**Updated recommendation:** keep existing seller-api.ozon-logistics scope and infrastructure. Prioritize isolated ST100 payment-total feasibility; do not deploy JS price overrides, and do not open a new Ozon OAuth app.
