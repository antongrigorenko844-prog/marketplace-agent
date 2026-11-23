"""One-time Avito v2 profile and autoload readback after the second tools feed update.
No API writes, no disclosure of account email."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
OUT=ROOT/"data/avito_post_tools_readback_result.json"
TARGETS=["6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2","tool-dq200-acc-restore","tool-dsg-pressure-hp"]
def request(method,path,**args):
    try: return av._request(method,path,retries=1,**args),None
    except Exception as exc: return {},str(exc)[:180]
def main():
    profile,error=request("GET","/autoload/v2/profile")
    report={"checked_at_utc":datetime.now(timezone.utc).isoformat(),"mode":"read_only",
       "profile_error":error,"enabled":profile.get("autoload_enabled"),
       "feeds":[{"name":x.get("feed_name"),"url":x.get("feed_url")} for x in profile.get("feeds_data") or []],
       "schedule":profile.get("schedule"),"report_email_present":bool(profile.get("report_email")),
       "new_listing_status":{}}
    a,err=request("GET","/autoload/v4/uploads/current")
    report["current_upload"]={"error":err,"id":a.get("upload_id"),"status":a.get("status"),
        "started_at":a.get("started_at")}
    b,err=request("GET","/autoload/v4/uploads/last_successful")
    report["last_successful_upload"]={"error":err,"id":b.get("upload_id"),"status":b.get("status"),
        "started_at":b.get("started_at")}
    binding,err=request("GET","/autoload/v2/items/avito_ids",params={"query":",".join(TARGETS)})
    report["binding_error"]=err
    report["binding_items"]=[{"sku":x.get("ad_id"),"avito_id":x.get("avito_id")} for x in binding.get("items") or []]
    try:
        all_items=av.list_all_items(per_page=100,max_pages=10)
        report["seller_item_count"]=len(all_items)
        for x in all_items:
            t=str(x.get("title") or "").lower()
            if "6t30" in t or ("набор" in t and ("давлен" in t or "гидроаккумулятора" in t)):
                report["new_listing_status"][str(x.get("id"))]={"title":x.get("title"),
                    "status":x.get("status"),"price":x.get("price")}
    except Exception as exc:
        report["listing_error"]=str(exc)[:180]
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_SECOND_FEED_READBACK",json.dumps({"feeds":len(report["feeds"]),
         "names":[x.get("name") for x in report["feeds"]],
         "enabled":report["enabled"],
         "schedule":report["schedule"],
         "report_email_present":report["report_email_present"],
         "bindings":report["binding_items"],
         "upload":report["current_upload"]},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
