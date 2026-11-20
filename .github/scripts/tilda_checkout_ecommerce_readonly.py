"""LEOPARTS storefront read-only checkout/ecommerce diagnosis via Chromium.

No click, form submission, cart operation or payment performed.
No customer input or sensitive payment settings collected; only public UI
metadata and boolean tracking-code checks. Analytics requests are blocked.
"""
import asyncio, json, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

URL="https://leoparts-akpp.ru/mehatronik-dq200"
OUT=Path("data/direct_optimization_20261120/tilda_checkout_ecommerce_status.json")
def now():return datetime.now(timezone.utc).isoformat()
def save(x):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
async def main():
    data={"status":"started","mode":"public_browser_readonly",
          "site":URL,"counter":113839860,"started_utc":now(),
          "no_click_no_submissions_no_payments":True,
          "analytics_collection_blocked":True,"errors":[]}
    save(data)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--no-sandbox"])
        context=await browser.new_context(viewport={"width":1440,"height":920})
        async def intercept(route):
            u=urlsplit(route.request.url)
            h=(u.hostname or "").lower()
            if (h in ("mc.yandex.ru","mc.yandex.com") and
                    ("/watch/" in u.path or "/webvisor" in u.path or "/collect" in u.path)):
                await route.abort()
            elif h.endswith("google-analytics.com"):
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*",intercept)
        page=await context.new_page()
        try:
            response=await page.goto(URL,wait_until="domcontentloaded",timeout=60000)
            data["http_status"]=response.status if response else None
            await page.wait_for_timeout(8500)
            data["checkout"]=await page.evaluate(r"""() => {
                const win=window;
                const rec=document.getElementById('rec5260805101');
                const form=document.getElementById('form5260805101');
                const srcs=Array.from(document.scripts).map(s => s.textContent || '');
                const inline=srcs.join('\n');
                const payments=Array.from(form?.querySelectorAll('input[name="paymentsystem"]')||[])
                  .map(x=>{
                    let l=x.closest('label');
                    let id=x.id?document.querySelector('label[for="'+x.id+'"]'):null;
                    let text=((l||id)?.textContent||'').trim().replace(/\s+/g,' ');
                    return {
                      value:(x.value||'').slice(0,50),
                      visible_label:text.slice(0,90),
                      required:x.required||false
                    };
                  });
                return {
                    checkout_rec_present:!!rec,
                    checkout_form_present:!!form,
                    checkout_has_successbox:!!form?.querySelector('.js-successbox'),
                    checkout_payment_methods:payments,
                    payment_options_count:payments.length,
                    metrika_counter_mentioned:inline.includes('113839860'),
                    metrika_ecommerce_true:/\becommerce\s*:\s*true/.test(inline),
                    metrika_ecommerce_data_layer:/\becommerce\s*:\s*["']dataLayer["']/.test(inline),
                    site_data_layer_array:Array.isArray(win.dataLayer),
                    data_layer_items_count:Array.isArray(win.dataLayer)?win.dataLayer.length:0,
                    data_layer_has_ecommerce_item:Array.isArray(win.dataLayer)&&
                        win.dataLayer.some(i=>i&&typeof i==='object'&&Object.prototype.hasOwnProperty.call(i,'ecommerce')),
                    tilda_cart_runtime:typeof win.tcart__init==="function",
                    tilda_general_success_handler:typeof win.t_forms__handleSuccess==="function",
                    payment_start_url_in_scripts:inline.includes('/tilda/form5260805101/payment/'),
                    checkout_submit_goal_url_in_scripts:inline.includes('/tilda/form5260805101/submitted/'),
                    ecommerce_script_markers:{
                      tcart: inline.includes('tcart__init'),
                      dataLayer: inline.includes('dataLayer'),
                      ecommerce: inline.toLowerCase().includes('ecommerce')
                    }
                }
            }""")
            data["status"]="complete" if data["http_status"]==200 else "unexpected_http_status"
        except Exception as e:
            data["status"]="error"
            data["errors"].append(type(e).__name__+":"+str(e)[:120])
        finally:
            data["finished_utc"]=now()
            save(data)
            await browser.close()
    print("LEOPARTS ecommerce check",data["status"],
          "payment_methods",data.get("checkout",{}).get("payment_options_count"),
          "data_layer",data.get("checkout",{}).get("site_data_layer_array"),
          "errors",data["errors"],flush=True)
    return 0 if data["status"]!="error" else 1
if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
