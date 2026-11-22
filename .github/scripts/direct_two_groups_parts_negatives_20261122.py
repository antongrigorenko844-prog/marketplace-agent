"""LEOPARTS 2026-11-22: add generic parts exclusions to other two DQ200 groups.
Group-only writes, guarded against current manual keywords, campaign budget,
and existing with-board exclusions. Preserve all original negative terms.
No blind retry on uncertain writes.
"""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from copy import deepcopy
import json,os,sys,re,urllib.request,urllib.error

CID=716316525
TARGETS={
 5826171512:"DQ200 | Мехатроники без платы | Поиск",
 5826320196:"Название: DQ200 | Электронные платы | Поиск.",
}
WITH_BOARD=5826183946
PHRASES=["запчасти","автозапчасти"]
CAP=4000000000
API="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_recovery_20261122/other_two_generic_parts_exclusion.json")
CG={"SelectionCriteria":{"Ids":[CID]},
 "FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
 "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}
GG={"SelectionCriteria":{"CampaignIds":[CID]},
 "FieldNames":["Id","CampaignId","Name","Status","NegativeKeywords","RegionIds","NegativeKeywordSharedSetIds","TrackingParams"],
 "Page":{"Limit":1000}}
KG={"SelectionCriteria":{"CampaignIds":[CID]},
 "FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","Status"],
 "Page":{"Limit":1000}}
UPDATE=None
SENT=False
def now():return datetime.now(timezone.utc).isoformat()
def save(obj):
 OUT.parent.mkdir(parents=True,exist_ok=True)
 tmp=OUT.with_suffix(".tmp")
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 tmp.replace(OUT)
def api(service,method,params):
 global SENT
 if method=="get":
  if (service,params) not in (("campaigns",CG),("adgroups",GG),("keywords",KG)):
   raise RuntimeError("GET outside allowed scope")
 elif method=="update" and service=="adgroups" and params==UPDATE and not SENT:
  SENT=True
 else:raise RuntimeError("Unapproved update or retry")
 token=os.getenv("YANDEX_DIRECT_TOKEN")
 if not token:raise RuntimeError("Missing Direct token")
 hdr={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
 if os.getenv("YANDEX_DIRECT_LOGIN"):hdr["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
 req=urllib.request.Request(API+service,headers=hdr,method="POST",
  data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode())
 try:
  with urllib.request.urlopen(req,timeout=60) as resp:
   blob=resp.read(4*1024*1024+1)
   if len(blob)>4*1024*1024:raise RuntimeError("Oversize response")
   obj=json.loads(blob)
 except urllib.error.HTTPError as e:
  code=e.code;e.close();raise RuntimeError("API HTTP "+str(code)) from None
 except Exception:raise RuntimeError("Transport or parse failure; do not retry") from None
 if obj.get("error"):raise RuntimeError("API code "+str(obj["error"].get("error_code")))
 result=obj.get("result")
 if not isinstance(result,dict) or result.get("LimitedBy"):raise RuntimeError("Missing or paginated result")
 return result
def read():
 c=api("campaigns","get",CG).get("Campaigns")
 g=api("adgroups","get",GG).get("AdGroups")
 k=api("keywords","get",KG).get("Keywords")
 if not isinstance(c,list) or len(c)!=1 or not isinstance(g,list) or len(g)!=6 or not isinstance(k,list) or len(k)!=272:
  raise RuntimeError("Unexpected campaign/adgroup/keyword count")
 c=c[0]
 if (c.get("Id")!=CID or c.get("Name")!="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест" or
  c.get("State")!="ON" or c.get("Type")!="UNIFIED_CAMPAIGN"):
  raise RuntimeError("Campaign identity differs")
 s=(c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
 if (((s.get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit")!=CAP or
  (s.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION" or
  (s.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"):
  raise RuntimeError("Budget cap/strategy baseline changed")
 neg=(c.get("NegativeKeywords") or {}).get("Items")
 if not isinstance(neg,list) or len(neg)<626:raise RuntimeError("Campaign negative inventory differs")
 if len({x.get("Id") for x in k})!=272 or any(x.get("CampaignId")!=CID for x in k+g):
  raise RuntimeError("Cross-campaign or duplicate-keyword data")
 return c,{x["Id"]:x for x in g},{x["Id"]:x for x in k}
def norm_group(g):
 v=deepcopy(g)
 v.pop("Status",None)
 v["NegativeKeywords"]={"Items":sorted((g.get("NegativeKeywords") or {}).get("Items") or [])}
 return v
def run():
 global UPDATE
 result={"status":"preflight","scope":"other_two_DQ200_groups_only","campaign":CID,
  "target_groups":list(TARGETS),"phrases":PHRASES,"attempted_write":False,
  "started_utc":now(),"errors":[]}
 save(result)
 try:
  if OUT.exists():
   old=json.loads(OUT.read_text(encoding="utf-8"))
   if old.get("attempted_write"):raise RuntimeError("Journal indicates prior write; no retry")
  c,g,k=read()
  if any(i not in g for i in TARGETS) or WITH_BOARD not in g:raise RuntimeError("Target groups not found")
  wb=(g[WITH_BOARD].get("NegativeKeywords") or {}).get("Items") or []
  if not all(word in wb for word in PHRASES):
   raise RuntimeError("Expected with-board generic exclusions not confirmed")
  result["with_board_already_applied"]=True
  changes=[]
  protected={}
  for gid,expected_name in TARGETS.items():
   gr=g[gid]
   if gr.get("Name")!=expected_name:raise RuntimeError("Unexpected target group "+str(gid))
   before=(gr.get("NegativeKeywords") or {}).get("Items") or []
   if len(set(before))!=len(before):raise RuntimeError("Duplicate group negatives "+str(gid))
   manual=[x["Keyword"] for x in k.values() if x["AdGroupId"]==gid and x["Keyword"]!="---autotargeting"]
   overlaps=[x for x in manual if any(p in set(re.findall(r"[a-zа-яё0-9]+",x.lower())) for p in PHRASES)]
   if overlaps:raise RuntimeError("Protected manual keyword overlap group "+str(gid)+": "+str(overlaps[:3]))
   protected[str(gid)]={"manual_keywords":len(manual),"negative_before":before}
   after=before+[p for p in PHRASES if p not in before]
   if len(set(after))!=len(after):raise RuntimeError("Duplicate output negatives")
   if after!=before:changes.append({"Id":gid,"NegativeKeywords":{"Items":after}})
  result["protected"]=protected
  result["planned_changes"]=changes
  if not changes:
   result["status"]="already_applied_verified"
   return 0
  if len(changes)!=2:raise RuntimeError("Partial prior application; no update")
  UPDATE={"AdGroups":changes}
  save(result)
  latest_c,latest_g,latest_k=read()
  if c!=latest_c or g!=latest_g or k!=latest_k:
   raise RuntimeError("Concurrent change detected before write")
  result["attempted_write"]=True;result["write_started_utc"]=now();save(result)
  try:
   response=api("adgroups","update",UPDATE)
   rows=response.get("UpdateResults") or []
   result["api_accepted"]=len(rows)==2 and {x.get("Id") for x in rows}==set(TARGETS) and all(not x.get("Errors") for x in rows)
   result["api_warnings"]=[{"id":x.get("Id"),"warnings":x.get("Warnings")} for x in rows if x.get("Warnings")]
  except Exception as e:result["errors"].append(str(e)[:250])
  a,ag,ak=read()
  current_groups={};correct=True
  for ch in changes:
   gid=ch["Id"]
   desired=ch["NegativeKeywords"]["Items"]
   actual=(ag[gid].get("NegativeKeywords") or {}).get("Items") or []
   expected=deepcopy(g[gid]);expected["NegativeKeywords"]={"Items":desired}
   group_ok=(Counter(actual)==Counter(desired) and norm_group(ag[gid])==norm_group(expected))
   correct=correct and group_ok
   current_groups[str(gid)]={"group_ok":group_ok,"terms":actual}
  other_ok=all(norm_group(g[gid])==norm_group(ag[gid]) for gid in g if gid not in TARGETS)
  result.update(after_groups=current_groups,other_groups_unchanged=other_ok,
    campaign_unchanged=(c==a),all_keywords_unchanged=(k==ak),verified_utc=now())
  if correct and other_ok and c==a and k==ak:
   result["status"]="applied_verified";return 0
  result["status"]="requires_review";return 1
 except Exception as e:
  result["status"]="blocked_or_unverified"
  result["errors"].append(str(e)[:280]);return 1
 finally:
  result["finished_utc"]=now();save(result)
  print("DQ200 generic parts exclusions",result["status"],"write",result["attempted_write"],flush=True)
if __name__=="__main__":sys.exit(run())
