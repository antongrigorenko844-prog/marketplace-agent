"""Public LEOPARTS cart→dataLayer smoke test. No form submissions or payments.

Runs in a fresh isolated Chromium profile, blocks every non-GET request and
all analytics collection URLs. Never sends conversion tracking hits.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

URL = "https://leoparts-akpp.ru/mehatronik-dq200"
OUT = Path("data/direct_optimization_20261120/tilda_cart_datalayer_readonly.json")
SELECTORS = (
    ".js-store-prod-btn",
    ".t-store__card__btn .t-btn",
    ".t-store__card__btn",
    ".t-store__card__btn-wrapper .t-btn",
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def write(doc):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)


async def snapshot(page):
    return await page.evaluate("""() => {
      const layer = Array.isArray(window.dataLayer) ? window.dataLayer : [];
      const events = layer.filter(x => x && typeof x === 'object' && x.ecommerce)
        .map(x => {
          const e = x.ecommerce || {};
          const kinds = Object.keys(e).filter(k => k !== 'currencyCode');
          return {
            actions: kinds,
            currency: typeof e.currencyCode === 'string' ? e.currencyCode.slice(0, 5) : null,
            item_counts: Object.fromEntries(kinds.map(k => {
              const obj = e[k];
              return [k, Array.isArray(obj?.products) ? obj.products.length : 0];
            }))
          };
        });
      return {
        data_layer_exists: Array.isArray(window.dataLayer),
        data_layer_length: layer.length,
        events,
        cart_runtime: typeof window.tcart__init === 'function',
        cart_form_present: Boolean(document.getElementById('form5260805101')),
        counter_code_present: [...document.scripts].some(x =>
          (x.textContent || '').includes('113839860')),
      };
    }""")


async def run():
    result = {
        "status": "started",
        "mode": "isolated_browser_no_analytics_no_submissions",
        "url": URL,
        "started_utc": utc(),
        "analytics_requests_blocked": 0,
        "non_get_requests_blocked": 0,
        "checkout_submit_attempts": 0,
        "payments_attempted": 0,
        "metrika_conversions_sent": 0,
        "errors": [],
    }
    write(result)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        async def intercept(route):
            req = route.request
            u = urlsplit(req.url)
            host = (u.hostname or "").lower()
            if req.method not in ("GET", "HEAD"):
                result["non_get_requests_blocked"] += 1
                await route.abort()
            elif (
                host in ("mc.yandex.ru", "mc.yandex.com", "www.google-analytics.com", "google-analytics.com")
                or host.endswith(".doubleclick.net")
                or "metrika" in host
            ):
                result["analytics_requests_blocked"] += 1
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*", intercept)
        page = await context.new_page()
        try:
            response = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            result["http_status"] = response.status if response else None
            await page.wait_for_timeout(7500)
            result["before"] = await snapshot(page)
            counts = {}
            clicked = False
            for selector in SELECTORS:
                loc = page.locator(selector)
                count = min(await loc.count(), 25)
                counts[selector] = count
                for i in range(count):
                    btn = loc.nth(i)
                    if not await btn.is_visible():
                        continue
                    # Only product-card controls; no forms, buy-now or payment buttons.
                    is_card = await btn.evaluate("""el => !!el.closest(
                      '.t-store__card, .js-store-prod-btn, .t-store__card__btn-wrapper'
                    )""")
                    if not is_card:
                        continue
                    label = (await btn.inner_text(timeout=3000)).strip().lower()
                    if not any(w in label for w in ("корзину", "купить", "в корзину")):
                        continue
                    result["clicked_element"] = {
                        "selector": selector,
                        "label": label[:60],
                        "index": i,
                    }
                    await btn.click(timeout=8000)
                    clicked = True
                    await page.wait_for_timeout(2000)
                    break
                if clicked:
                    break
            result["candidate_counts"] = counts
            result["product_control_clicked"] = clicked
            result["after"] = await snapshot(page)
            before = result["before"]["events"]
            after = result["after"]["events"]
            # E-commerce is expected to be appended; never claim event was sent to Yandex.
            delta = after[len(before):] if after[:len(before)] == before else after
            result["new_data_layer_actions"] = [action for event in delta for action in event["actions"]]
            result["add_to_cart_payload_observed"] = "add" in result["new_data_layer_actions"]
            result["purchase_payload_observed"] = "purchase" in result["new_data_layer_actions"]
            result["status"] = "complete" if result["http_status"] == 200 else "unexpected_http_status"
        except Exception as e:
            result["status"] = "error"
            result["errors"].append(type(e).__name__ + ":" + str(e)[:170])
        finally:
            result["finished_utc"] = utc()
            write(result)
            await browser.close()
    print("TILDA_CART_DATALAYER", result["status"],
          "clicked", result.get("product_control_clicked"),
          "add_payload", result.get("add_to_cart_payload_observed"),
          "errors", result["errors"], flush=True)
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
