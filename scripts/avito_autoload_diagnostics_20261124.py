"""Read-only Avito Autoload diagnostic for LEOPARTS after owner Excel changes.

Checks configured feed, last/current upload, and 3 delayed title changes.
Never initiates upload; never writes seller data, site, stock or feed.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
OUT=ROOT/"data/avito_autoload_diagnostics_20261124.json"
TARGETS={
  8231885696:"Оригинальная крышка DQ200 + усиленная прокладка",
  8449035864:"Пластина соленоидов DSG7 DQ200 оригинал VAG",
  8394615261:"Сальники толкателей вилок DSG7 DQ200, 4 шт",
}
OPS={
  "profile":"/autoload/v2/profile",
  "current":"/autoload/v4/uploads/current",
  "last_successful":"/autoload/v4/uploads/last_successful",
  "history":"/autoload/v4/uploads",
}
def safe_read(name,path):
    try:
        data=av._request("GET",path,
               params={"page":1,"perPage":10} if name=="history" else None,
               retries=1)
        return {"ok":True,"data":data}
    except Exception as e:
        return {"ok":False,"error_type":type(e).__name__,
                "error":str(e)[:280]}
def main():
    result={"utc":datetime.now(timezone.utc).isoformat(),
            "mode":"read_only","seller_writes":0,
            "checks":{key:safe_read(key,url) for key,url in OPS.items()}}
    try:
        rows=av.list_all_items(per_page=25,max_pages=40)
        ads={int(row["id"]):row for row in rows if row.get("id") is not None}
        result["seller_counts"]={"total":len(rows),"unique":len(ads)}
        result["titles"]={
          str(id):{"desired":title,"live_title":ads.get(id,{}).get("title"),
                   "live_price":ads.get(id,{}).get("price"),
                   "status":ads.get(id,{}).get("status"),
                   "title_verified":ads.get(id,{}).get("title")==title}
          for id,title in TARGETS.items()
        }
    except Exception as ex:
        result["seller_error"]=str(ex)[:260]
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("AUTOLOAD_DIAGNOSTIC",json.dumps({
       "status":"completed","checks":
          {k:v["ok"] for k,v in result["checks"].items()},
       "titles":{k:v["title_verified"] for k,v in result.get("titles",{}).items()}},
       ensure_ascii=False),flush=True)
if __name__=="__main__":main()
