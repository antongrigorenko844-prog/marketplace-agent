# Ozon Delivery: safety gates before a Tilda ST100 staging install

## Evidence from inspected third-party widget (reference only, not copied)
The supplied Capyflow w.js demonstrates that its Tilda adapter calls internal (not documented public) functions `tcart_newDelivery.updatePriceValueInRadio`, `tcart__updateDelivery`, and writes pickup metadata via DOM attributes. This is **evidence of an implementation technique**, not evidence that Tilda officially supports or guarantees it. Its code also removes other pickup attributes, which could conflict with Yandex/CDEK. Do not copy that behavior.

## Required non-production integration test matrix
| Scenario | Expected result |
|---|---|
| Yandex selected -> open/close Ozon UI | Yandex delivery attributes and price unchanged |
| CDEK selected -> open/close Ozon UI | CDEK delivery attributes and price unchanged |
| Ozon selected -> choose point -> quote | Only Ozon price and point updated |
| Ozon -> Yandex -> Ozon | Previous Ozon point/quote invalidated, Yandex untouched |
| Ozon -> CDEK -> Ozon | Previous Ozon point/quote invalidated, CDEK untouched |
| Change goods, quantity, city, weight | Invalidate old quote; no stale price accepted |
| Delayed quote after changing point | Ignore old response |
| API failure or timeout | Block Ozon checkout; do not fall back to invented price |
| Missing parcel dimensions | Reject or require verified fallback configuration; no guessed shipping promise |
| Payment submission | Verify charged amount server-side equals confirmed checkout amount |
| Submitted Tilda order | Verified pickup ID/address and amount persisted |
| Cart rerender / reload / mobile | No duplicated radio, no missing handler, no infinite rerender |
| Third-party widget JS fails to load | Existing Yandex/CDEK checkout unaffected |
| Ozon backend unavailable | Existing Yandex/CDEK checkout unaffected |

## Hard safety rules
- Preserve existing production HEAD buy-to-cart JavaScript **byte-for-byte**.
- Do not modify existing Yandex/CDEK delivery settings or `vk-proxy`.
- Do not write to `data-pickup-*` or `tildadelivery-pickup-name` until an Ozon point is actively selected and staging confirms no collisions.
- No global `click` or `submit` interception that can block non-Ozon carriers.
- Never use browser-supplied shipping prices as backend authorization to create a shipment.
- If payment total cannot be proven correct after an Ozon price update, do not ship to production.
- Release only after explicit approval and a tested rollback that disables *only* the Ozon script.

## Evidence still needed
- A real, isolated Tilda ST100 page DOM and its actual payment submission behavior.
- Ozon production API credentials and a verified, authorized quote response.
- Confirmation of checkout amount and order metadata in Tilda staging.
- Evidence that Yandex and CDEK remain functional through all scenarios.

No deployment or production checkout change is authorized by this document.
