# ST100 shipping feasibility — evidence and decision (2026-11-17)

Official source: https://help-ru.tilda.cc/online-store/shipping

## Documented behavior
1. ST100 **simple delivery options** add a fixed numeric delivery charge to the cart total (label=amount).
2. ST100 **advanced mode** shows only services configured in site delivery settings; Yandex Delivery and CDEK can coexist with manually configured delivery methods.
3. Advanced mode allows **custom pickup-point delivery** with city, addresses, coordinates, estimated time and **configured price**. Each city requires its own delivery variant.
4. Payment systems can be enabled/disabled for each delivery method.
5. This documentation **does not describe** a public third-party carrier plug-in interface or an API to update the trusted ST100 payment total dynamically.

## Recommendation for Leoparts
**Option A: staging-only custom pickup-point shipping with fixed prices** is the lowest-cost, lowest-change-risk path to verify that a third delivery method can coexist with Yandex/CDEK and preserve the existing Tilda payment flow.

**Not a complete Ozon integration.** Tilda's manually entered points are not automatically synchronized with Ozon's live points. Never advertise them as current Ozon points without checking current eligibility and address. Fixed rates must not be presented as Ozon live rates. Do not create a live shipment until official Ozon eligibility, buyer phone, stock and price checks succeed.

**Option B: true live Ozon tariff** requires confirmed Tilda supported integration or separate checkout. The existing docs do not establish such a mechanism. Do not hack ST100 payment total with browser JS.

## Validation sequence on non-public staging copy only
- Reproduce current Yandex/CDEK shipping and payment settings without touching production.
- Add a **clearly marked test** custom pickup-point method in staging.
- Verify delivery charge appears in order total and payment, and pickup selection survives submission.
- Verify existing carriers still work.
- Verify Ozon's seller logistics API can fulfill a paid order to that actual point.
- Only then choose a production approach, with owner approval.

## Hard stop
No live site modifications, no customer-visible test points, no payment attempts, no shipment creation until external API and staging checks pass.
