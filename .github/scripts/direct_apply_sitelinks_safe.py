"""Apply approved DQ200 sitelinks to three known shopping ads, with live guards and rollback.
No budget, bids, feed titles, filters, negatives or targeting are touched.
"""
import json, os, urllib.request, urllib.error, re, sys
from pathlib import Path
CID=716316525
EXPECTED={1927186476439690942:5826171512,1927189770678081700:5826183946,1927190920657966611:5826320196}
BASE="https://leoparts-akpp.ru/mehatronik-dq200"
LINKS=[
 {"Title":"Мехатроники без платы","Href":BASE+"#bez-platy","Description":"Мехатроники DSG7 DQ200 без электронной платы"},
 {"Title":"Мехатроники с платой","Href":BASE+"#s-platoi","Description":"Мехатроники DSG7 DQ200 с электронной платой"},
 {"Title":"Электронные платы DQ200","Href":BASE+"#platy","Description":"Платы управления мехатроника DSG7 DQ200"}]
OUT=Path("data/direct_sitelinks_apply_20261119.json")
def save(d):OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
H={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):H["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
def call(service,method,params,version="v501"):
 req=urllib.request.Request("https://api.direct.yandex.com/"+version+"/"+service,data=json.dumps({"method":method,"params":params}).encode(),headers=H)
 try:
  with urllib.request.urlopen(req,timeout=90) as res: d=json.load(res)
 except urllib.error.HTTPError as e: raise RuntimeError(f"{service}.{method} HTTP {e.code}: "+e.read().decode(errors="replace")[:1200])
 if d.get("error"):raise RuntimeError(f"{service}.{method}: "+json.dumps(d["error"],ensure_ascii=False))
 return d.get("result",{})
def snapshot():
 r=call("ads","get",{"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","CampaignId","AdGroupId","Type","Status","State"],"ShoppingAdFieldNames":["SitelinkSetId","FeedId","GenerationScopes","FeedProcessingStatus"],"Page":{"Limit":1000}})
 return r.get("Ads",[])
def checked_result(res,key):
 a=res.get(key,[])
 if len(a)!=1 or a[0].get("Errors") or not a[0].get("Id"):raise RuntimeError(json.dumps(res,ensure_ascii=False))
 return a[0]["Id"]
status={"status":"preflight","campaign":CID,"requested_links":LINKS,"changed":[]}
try:
 assert all(len(x["Title"])<=30 and len(x["Description"])<=60 for x in LINKS)
 # A page/anchor failure must abort before any changes.
 req=urllib.request.Request(BASE,headers={"User-Agent":"Mozilla/5.0"})
 with urllib.request.urlopen(req,timeout=30) as resp:
  html=resp.read().decode("utf-8","ignore")
  if resp.status!=200:raise RuntimeError("Landing HTTP not 200")
 for anchor in ("bez-platy","s-platoi","platy"):
  if not re.search(r'(?:id|name)\\s*=\\s*["\\']'+re.escape(anchor)+r'["\\']',html,re.I):
   raise RuntimeError("Missing anchor: "+anchor)
 before=snapshot()
 if len(before)!=3 or {x["Id"]:x["AdGroupId"] for x in before}!=EXPECTED or any(x["Type"]!="SHOPPING_AD" for x in before):
  raise RuntimeError("Live ad inventory differs from expected; abort")
 status["before"]=before
 save(status)
 existing={x["ShoppingAd"].get("SitelinkSetId") for x in before}
 # Do not overwrite any existing sitelinks without a separately reviewed merge.
 if any(existing):raise RuntimeError("Existing sitelinks found; do not overwrite")
 result=call("sitelinks","add",{"SitelinksSets":[{"Sitelinks":LINKS}]},"v5")
 sid=checked_result(result,"AddResults")
 status["new_sitelinks_set_id"]=sid
 save(status)
 for ad in before:
  result=call("ads","update",{"Ads":[{"Id":ad["Id"],"ShoppingAd":{"SitelinkSetId":sid}}]})
  ar=result.get("UpdateResults",[])
  if len(ar)!=1 or ar[0].get("Errors"):raise RuntimeError("Ads.update failed: "+json.dumps(result,ensure_ascii=False))
  status["changed"].append(ad["Id"]);save(status)
 after=snapshot()
 if any(x["ShoppingAd"].get("SitelinkSetId")!=sid for x in after):
  raise RuntimeError("Post-apply verification failed")
 status["after"]=after;status["status"]="verified";save(status)
except Exception as e:
 status["error"]=str(e)
 if status.get("changed"):
  rollback=[]
  for aid in reversed(status["changed"]):
   try:
    # Existing set IDs were all empty; null restores previous value.
    r=call("ads","update",{"Ads":[{"Id":aid,"ShoppingAd":{"SitelinkSetId":None}}]})
    rollback.append({"ad":aid,"result":r})
   except Exception as er:rollback.append({"ad":aid,"error":str(er)})
  status["rollback"]=rollback
 status["status"]="failed";save(status)
 print("FAILED:",str(e))
 sys.exit(1)
print("VERIFIED sitelinks on",len(status["changed"]),"shopping ads")
