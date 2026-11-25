"""One scoped Direct change: exact reviewed non-target search queries only."""
import json,sys,subprocess,datetime
from copy import deepcopy
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent))
from direct_mass_negatives_apply_20261120 import Client,campaign_read,phrases,stable,atomic_save,now,CID,GROUP_GET,ADS_GET
from direct_semantic_filter_20261124 import PROTECTED,overlap,cost
import direct_preflight_20261120 as d
OUT=Path("data/direct_performance_20261125/exact_query_filter.json")
BASE=Path("data/direct_performance_20261125/live_30d.json")
CANDIDATES={"авто запчасти":"Общий поиск автозапчастей","контрактные запчасти":"Общий поиск контрактных деталей","платы микросхемы":"Общая электроника","неисправный мехатроник dq 500 купить":"Неисправный узел другой КПП","устройство dsg":"Общая теория без указания детали"}
def norm(x):return " ".join(str(x).lower().replace("ё","е").split())
def run():
 if OUT.exists():raise ValueError("Existing journal, no replay")
 j={"campaign_id":CID,"status":"preflight","started_utc":now(),"write_attempted":False,"reviewed":CANDIDATES}
 atomic_save(OUT,j)
 try:
  src=json.loads(BASE.read_text())
  if CID!=716316525 or src["campaign_id"]!=CID or not src["sections"]["queries"]["data"]["coverage_complete"]:raise ValueError("No complete scoped evidence")
  if src["today"]!=datetime.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat():raise ValueError("Old source")
  csrc=json.loads(Path("data/direct_current_20261125_live.json").read_text())
  expected=csrc["supplemental"]["campaign_negatives"]["data"]["negatives"]
  api=Client("apply");before=campaign_read(api);old=phrases(before)
  if set(old)!=set(expected) or before.get("State")!="ON":raise ValueError("Negative baseline changed")
  if before["UnifiedCampaign"]["BiddingStrategy"]["Search"]["HighestPosition"].get("WeeklySpendLimit")!=4000000000:raise ValueError("Budget changed")
  keys=d.keywords();groups=api("adgroups","get",GROUP_GET)["AdGroups"];ads=api("ads","get",ADS_GET)["Ads"]
  if len(keys)!=272 or len(groups)!=6 or len(ads)!=8:raise ValueError("Inventory changed")
  if stable(sorted(keys,key=lambda x:x["Id"]))!=stable(sorted(src["sections"]["keywords"]["data"],key=lambda x:x["Id"])):raise ValueError("Keyword drift")
  allpaid=src["sections"]["queries"]["data"]["paid_rows"]
  manual=[x["Keyword"] for x in keys if x["Keyword"]!="---autotargeting"]
  j["checks"]={};add=[]
  for q in CANDIDATES:
   p='"'+q+'"'
   rows=[x for x in allpaid if norm(x["Query"])==norm(q)]
   cv=[d.num(x.get("Conversions")) for x in rows]
   conflicts=[x for x in manual+PROTECTED if overlap(p,x)]
   recent=[x for x in rows if x["Date"]>="2026-11-24"]
   allowed=bool(recent) and bool(cv) and all(x==0 for x in cv) and not conflicts and p not in old
   j["checks"][q]={"observed_rows":len(rows),"recent_cost":str(sum(d.num(x["Cost"]) or 0 for x in recent)),"all_reported_conversions_zero":bool(cv) and all(x==0 for x in cv),"conflicts":conflicts,"decision":"add_exact" if allowed else "hold"}
   if allowed:add.append(p)
  target=old+add;j.update(before=old,additions=add)
  if not add:j["status"]="nothing_to_apply";return
  if cost(target)>19000 or any(not(x.startswith('"') and x.endswith('"')) for x in add):raise ValueError("Scope or length guard")
  if campaign_read(api)!=before or stable(d.keywords())!=stable(keys):raise ValueError("Concurrent change")
  j["write_attempted"]=True;atomic_save(OUT,j)
  subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True,timeout=180)
  api.allowed_update={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":target}}]}
  try:j["api_result"]=api("campaigns","update",api.allowed_update)
  except Exception as e:j["api_error"]=d.scrub(e)
  after=campaign_read(api);b=deepcopy(before);a=deepcopy(after);b.pop("NegativeKeywords",None);a.pop("NegativeKeywords",None)
  j["after"]=phrases(after)
  j["protected"]={"campaign_other_fields":stable(a)==stable(b),"keywords":stable(d.keywords())==stable(keys),"groups":stable(api("adgroups","get",GROUP_GET)["AdGroups"])==stable(groups),"ads":stable(api("ads","get",ADS_GET)["Ads"])==stable(ads),"all_old_negatives_preserved":set(old)<=set(j["after"]),"only_reviewed_additions":set(j["after"])==set(target)}
  j["status"]="verified" if all(j["protected"].values()) else "requires_review"
 except Exception as e:j["status"]="blocked_or_unverified";j["error"]=d.scrub(e)
 finally:
  j["finished_utc"]=now();atomic_save(OUT,j)
  print(json.dumps({k:j.get(k) for k in ("status","checks","additions","protected","error")},ensure_ascii=False))
 if j["status"] not in ("verified","nothing_to_apply"):raise RuntimeError(j["status"])
if __name__=="__main__":run()
