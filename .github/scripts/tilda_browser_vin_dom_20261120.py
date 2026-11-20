"""Public read-only browser inspection of Tilda form after script rendering.

No typing, clicking, submitting, or changing site state.
Yandex analytics requests are blocked to avoid recording a test visit.
Only high-level structural elements (no personal values or raw HTML) stored.
"""
import asyncio, json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

OUT=Path("data/direct_optimization_20261120/tilda_browser_vin_post_publish_20261120.json")
URL="https://leoparts-akpp.ru/mehatronik-dq200"
async def probe():
    doc={"status":"running","site":URL,"mode":"public_browser_read_only",
         "counter_id":113839860,"started_utc":datetime.now(timezone.utc).isoformat(),
         "no_clicks_or_submissions":True,"analytics_requests_blocked":True,"errors":[]}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    def save():OUT.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save()
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--no-sandbox"])
        context=await browser.new_context(viewport={"width":1365,"height":900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
        async def block_analytics(route):
            url=route.request.url.lower()
            if any(x in url for x in ("mc.yandex.ru","metrika.yandex","google-analytics","googletagmanager","/watch/113839860")):
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*",block_analytics)
        page=await context.new_page()
        try:
            resp=await page.goto(URL,wait_until="domcontentloaded",timeout=60000)
            doc["http_status"]=resp.status if resp else None
            await page.wait_for_timeout(10000)
            doc["browser_url"]=page.url.split("?")[0].split("#")[0]
            doc["dom"]=await page.evaluate(r"""() => {
              const getBlock = (id) => {
                let rec=document.getElementById("rec"+id);
                if(!rec) return {exists:false};
                let forms=Array.from(rec.querySelectorAll("form"));
                let inputs=Array.from(rec.querySelectorAll("input,textarea,select"));
                let buttons=Array.from(rec.querySelectorAll("button,input[type=submit],.t-submit"));
                const names=(xs,max)=>xs.slice(0,max).map(x=>({
                     tag:x.tagName.toLowerCase(),
                     type:(x.type||"").slice(0,35),
                     field_name:(x.getAttribute("name")||"").slice(0,60),
                     css_class:(x.className&&typeof x.className==="string"?x.className:"").slice(0,110),
                     id:(x.id||"").slice(0,60)
                }));
                return {exists:true, data_record_type:rec.getAttribute("data-record-type"),
                  forms:forms.slice(0,10).map(f=>({id:f.id,css_class:String(f.className).slice(0,120),
                    action: f.getAttribute("action")? "[nonempty]" : "",
                    has_successbox:!!f.querySelector(".js-successbox"),
                    num_fields:f.elements?f.elements.length:null})),
                  fields:names(inputs,20),buttons:names(buttons,15),
                  zero_block_input_containers:rec.querySelectorAll(".tn-form,.tn-atom__inputs-data").length,
                  html_form_count:forms.length,
                  button_count:buttons.length,
                  has_tildaform_runtime:!!(rec.querySelector(".js-form-proccess")),
                  rec_visible:!!(rec.offsetWidth||rec.offsetHeight||rec.getClientRects().length)
                };
              };
              const t396=window.t396_onSuccess;
              const generic=window.t_forms__handleSuccess;
              return {title:document.title.slice(0,100),
                ready_state:document.readyState,
                scripts_count:document.scripts.length,
                counter_present_in_dom:document.documentElement.innerHTML.includes("113839860"),
                vin_success_snippet_marker_present:document.documentElement.innerHTML.includes("LEOPARTS_VIN_SUCCESS_5246286101"),
                vin_success_script_event_present:Array.from(document.scripts).some(s => s.textContent && s.textContent.includes("tildaform:aftersuccess") && s.textContent.includes("LEOPARTS_VIN_SUCCESS_5246286101")),
                vin_success_form_bound:document.getElementById("form5246286101")?.dataset.leopartsVinGoalBound==="1",
                tilda_script_loaded:typeof window.t_onReady==="function",
                t396_success_hook_type:typeof t396,
                tilda_general_success_handler_type:typeof generic,
                rec_vin:getBlock("5246286101"),
                rec_cart:getBlock("5260805101"),
                all_forms:Array.from(document.querySelectorAll("form")).map(f=>({
                  id:f.id,class:String(f.className).slice(0,100),
                  parent_rec_id:(f.closest("[id^=rec]")||{}).id||""})).slice(0,25),
                record_count:document.querySelectorAll('[id^="rec"]').length
              };
            }""")
        except Exception as e:
            doc["status"]="failed";doc["errors"].append(type(e).__name__+":"+str(e)[:120])
        finally:
            if doc.get("errors"):
                doc["status"]="check_failed"
            elif doc.get("http_status")!=200:
                doc["status"]="site_unavailable"
            elif (doc.get("dom",{}).get("vin_success_script_event_present")
                  and doc.get("dom",{}).get("vin_success_form_bound")):
                doc["status"]="deployed_vin_handler_verified_no_form_submission"
            else:
                doc["status"]="handler_not_found_in_rendered_page"
            doc["finished_utc"]=datetime.now(timezone.utc).isoformat()
            save()
            await browser.close()
    print("Tilda Browser VIN DOM",doc["status"],
          "vin_forms",doc.get("dom",{}).get("rec_vin",{}).get("html_form_count"),
          "cart_forms",doc.get("dom",{}).get("rec_cart",{}).get("html_form_count"),
          "errors",doc["errors"],flush=True)
    return 0 if doc["status"]!="check_failed" else 1

if __name__=="__main__":sys.exit(asyncio.run(probe()))
