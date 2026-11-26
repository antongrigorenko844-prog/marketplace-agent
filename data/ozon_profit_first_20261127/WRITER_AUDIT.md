# Ozon price/promotion writer audit — read only

Scanned main: `482ea7b0c247aa2e1935129ced0ead07ddfb0de0`. Reviewed draft PR35: `d8ca4343b97fe0ba6c9137e56dc5b295d35ed7f2`. All 744 non-test Python, shell and GitHub workflow files were read. No marketplace writes, workflows or remote mutations were performed.

## Main conclusion

The twice-daily conflict is real in configuration: `.github/workflows/sync-prices.yml` schedules `0 5,17 * * *` UTC (08:00 and 20:00 Moscow), invokes `run-command.yml` with `sync-ozon-prices`, and restores master-catalog price/old_price. Shared concurrency only serializes it; it does not prevent the next run overwriting an approved promotion price. Recent execution could not be verified: GitHub connector rejected workflow endpoint reads. This is a configured schedule, not an assertion that the last run succeeded.

PR35 is incomplete isolation: 11 price submission sites, including 4 direct `_post` bypasses, and 15 `import_products` sites that can carry price. The guard in `update_prices` alone does not establish a single writer. Empty managed_skus means no current listing is protected by this draft.

## Price writers

| Path / line | Type | Entry point | PR35 |
| --- | --- | --- | --- |
| main.py:3594 | scheduled_catalog_price_restore | .github/workflows/sync-prices.yml → run-command.yml; cron 0 5,17 * * * | covered via update_prices and unmanaged_ids |
| main.py:3648 | min_equals_current_price | cmd_ozon_leave_boosting; manual tools.yml | covered via update_prices and unmanaged_ids |
| main.py:3793 | disable_auto_actions | manual tools.yml; cmd_ozon_leave_boosting | covered via update_prices and unmanaged_ids |
| scripts/approved_dq250_dq500_market_prices_20261122.py:145 | dated_scoped_price_writer | .github/workflows/approved-dq250-dq500-market-prices-20261122.yml | covered via update_prices |
| scripts/approved_rods_filters_20261122.py:220 | dated_scoped_price_writer | .github/workflows/approved-rods-filters-20261122.yml | covered via update_prices |
| scripts/approved_vtulki_klipsy_20261122.py:128 | dated_scoped_price_writer | .github/workflows/approved-vtulki-klipsy-20261122.yml | covered via update_prices |
| scripts/ea888_gen3_full_kit_ozon_finalize_20261123.py:82 | dated_scoped_price_writer | .github/workflows/ea888-gen3-filter-kit-ozon-finalize-20261123.yml | covered via update_prices |
| scripts/leoparts_market_prices_20261121.py:192 | direct_post_price_writer | .github/workflows/leoparts-market-prices-20261121.yml | BYPASS |
| scripts/ozon_101mm_price_apply_20261123.py:169 | direct_post_price_writer | .github/workflows/ozon-101mm-approved-price.yml | BYPASS |
| scripts/ozon_restore_0am325025_20261123.py:169 | direct_post_price_writer | .github/workflows/ozon-restore-0am325025-20261123.yml | BYPASS |
| scripts/ozon_seven_prices_apply_20261123.py:181 | direct_post_price_writer | .github/workflows/ozon-seven-approved-prices.yml | BYPASS |

## Full imports that carry commercial fields

| Path / line | Context |
| --- | --- |
| main.py:291 | cmd_push_ozon_cards |
| main.py:1399 | cmd_push_ozon_new_cards |
| main.py:1631 | cmd_push_new_product_all |
| main.py:2965 | cmd_ozon_create_missing |
| scripts/correct_package_dimensions.py:86 | dimensions, preserves read prices but still full import |
| scripts/ea888_gen3_full_kit_ozon_create_20261123.py:47 | new product |
| scripts/ea888_gen3_full_kit_ozon_finalize_20261123.py:64 | new product finalization |
| scripts/leoparts_ozon_6t30_gen2_safe_publish_20261123.py:152 | scoped creation |
| scripts/leoparts_ozon_gen2_moderation_fix_20261123.py:70 | moderation repair |
| scripts/leoparts_ozon_tcm_import_20261123.py:146 | scoped creation |
| scripts/leoparts_ozon_two_dsg_guarded_import_20261123.py:170 | scoped creation |
| scripts/ozon_category_seo_canary_20261125.py:140 | category/SEO, full existing-card payload |
| scripts/ozon_full_seo_20261125.py:401 | full existing-card SEO payload |
| scripts/product_publishing_v3_adapters.py:121 | OzonAdapter.submit; approved NEW SKU only; scheduled pending resume |
| scripts/repair_vint_ea888_ozon_apply_20261123.py:163 | existing-card repair |

These full imports are not evidence of unintended price changes by themselves. Existing-card content/dimension scripts often preserve freshly read prices. They remain potential stale-price replay and bypass paths, and must participate in the same transport guard/lock. The Ozon description guard is separate and must remain intact.

## Other material findings

- `_ozon_set_price_floor` writes min_price equal to live price. `cmd_ozon_leave_boosting` then disables auto-add for the whole catalog, even if old action reads failed. Tools exposes both manual commands.
- Current `cmd_update_cards` delegates to content_sync; the private composite `_cmd_update_cards_impl` remains in the file but no executable caller was found in scanned code. Do not claim the present content scheduler regularly invokes it.
- Current `auto-update-cards.yml` is content-only. The common 3-hour stock loop contains no Ozon price sync.
- Product Publishing v3 has a :13/:43 schedule but only resumes already authorized pending NEW SKU tasks. Preserve this flow and its existing approvals. It is not a blanket repricer of the current catalog.
- Legacy floor/auto-disable acknowledgements use `updated=True` as a missing-field default and do not require every requested SKU in the response. Empty or partial responses can falsely look successful.
- `get_action_products` uses old v1/offset semantics; `leave_boosting` tries float() on prices now represented as Money. Do not base managed writes on failed or stale reads.
- `deactivate_action_products` has no executable caller in the scanned repository. Its v2/v1 fallback is not proof those endpoints can remove unified-price promotions.
- `ozon_promos_diag.py` probes mutating endpoints directly with zero action ID/empty products. Keep it out of a strict read-only collector; new collector should allow only verified reads.
- Old workflow reruns pin old commits; new guards do not retroactively protect old code. Manual Seller UI and third-party integrations are outside repository visibility.

## Recommended integration boundary

After the Make or Buy adaptation decision, place the financial/identity guard at shared `ozon_client._post` before networking and cover prices, full imports and promotion mutations. Resolve product IDs to exact SKUs; never casefold IDs. Keep noncommercial content and stock flows separate. A read-only or missing draft must not globally block unrelated legacy work: validate protected-scope policy only when protection is enabled, and fail closed for protected scope.

Enable managed SKUs only after approved economics, current policy and a ready writer. One writer needs durable intended-before/after values, current-head/freshness checks, exact acknowledgements, API readback, reconcile-before-retry, safe rollback policy and an emergency stop. Retain profitable current state when fresh evidence is unavailable. Do not touch WB/Market/Avito/Tilda pricing.
