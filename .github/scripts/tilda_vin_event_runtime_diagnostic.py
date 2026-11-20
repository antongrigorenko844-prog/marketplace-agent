"""Observe browser-side VIN tracking WITHOUT any real or synthetic analytics hit.

Chromium loads the public page and Metrica tag JavaScript, but all analytics
data collection endpoints are blocked. To verify the listener, a local fake
function temporarily replaces window.ym and a synthetic Tilda success event is
dispatched on the test browser's form. No real form submission, user data, or
requests to Metrica reachGoal are sent.
"""
import asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

URL="https://leoparts-akpp.ru/mehatronik-dq200"
OUT=Path("data/direct_optimization_20261120/tilda_vin_event_runtime_diagnostic.json")
COUNTER=113839860
GOAL="LEOPARTS_VIN_SUCCESS_5246286101"
def now():return datetime.now(timezone.utc).isoformat()
def save(obj):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
async def main():
    result={"status":"running","site":URL,"counter":COUNTER,"goal":GOAL,
        "started_utc":now(),"mode":"isolated_browser_no_user_data",
        "real_form_submissions":0,"real_goal_hits_allowed":False,
        "analytics_collection_requests_blocked":0,
        "errors":[]}
    save(result)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=["--no-sandbox"])
        context=await browser.new_context(viewport={"width":1370,"height":900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")
        script_responses=[]
        async def block_collections(route):
            u=urlsplit(route.request.url)
            host=(u.hostname or "").lower()
            path=u.path.lower()
            if host in ("mc.yandex.ru","mc.yandex.com","yastatic.net") and (
                "/watch/" in path or "/webvisor" in path or "collect" in path):
                result["analytics_collection_requests_blocked"]+=1
                await route.abort()
            elif host.endswith("google-analytics.com") or host.endswith("googletagmanager.com"):
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*",block_collections)
        page=await context.new_page()
        def observed_response(r):
            try:
                u=urlsplit(r.url)
                if "yandex" in (u.hostname or "") and (
                    "metrika" in u.path.lower() or "tag.js" in u.path.lower()):
                    script_responses.append({"host":u.hostname,"path":u.path[:80],
                                             "status":r.status})
            except Exception:
                pass
        page.on("response",observed_response)
        try:
            resp=await page.goto(URL,wait_until="domcontentloaded",timeout=60000)
            result["http_status"]=resp.status if resp else None
            await page.wait_for_timeout(9000)
            # The synthetic success event is sent ONLY after wrapping window.ym.
            # Restoration is guaranteed by finally; no Tilda form submit is made.
            result["runtime"]=await page.evaluate(r"""() => {
                let f=document.getElementById("form5246286101");
                let original=window.ym;
                const hits=[];
                const baseline = {
                    form_present:!!f,
                    script_marker_present:document.documentElement.innerHTML.includes("LEOPARTS_VIN_SUCCESS_5246286101"),
                    listener_bound:f?.dataset.leopartsVinGoalBound==="1",
                    metrika_function_present:typeof original==="function",
                    original_metrika_has_queue:!!(original && original.a),
                    script_count:document.scripts.length
                };
                if (!f) return {baseline, synthetic_test:"skipped_no_form"};
                window.ym = function(...args){
                    hits.push({counter:args[0],command:args[1],
                               goal_identifier:args[2]});
                    // no network forwarding
                };
                try {
                    // Tilda event also has other existing listeners, but the
                    // local window.ym interception guarantees no analytics send.
                    f.dispatchEvent(new CustomEvent("tildaform:aftersuccess",
                                                     {bubbles:false}));
                } finally {
                    window.ym=original;
                }
                return {baseline,
                    synthetic_event_test_only:true,
                    isolated_calls_to_ym:hits,
                    correctly_emitted: hits.some(h => h.counter===113839860 &&
                      h.command==="reachGoal" &&
                      h.goal_identifier==="LEOPARTS_VIN_SUCCESS_5246286101")};
            }""")
            result["metrika_tag_responses"]=script_responses[:15]
            if result.get("http_status")!=200:
                result["status"]="site_unavailable"
            elif result["runtime"].get("correctly_emitted"):
                result["status"]="local_success_event_invokes_metrika_correctly"
            else:
                result["status"]="local_success_event_did_not_invoke_metrika"
        except Exception as e:
            result["status"]="error"
            result["errors"].append(type(e).__name__+": "+str(e)[:160])
        finally:
            result["finished_utc"]=now()
            save(result)
            await browser.close()
    print("VIN runtime",result["status"],"blocked_analytics",
          result["analytics_collection_requests_blocked"],flush=True)
    return 0 if result["status"]!="error" else 1
if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
