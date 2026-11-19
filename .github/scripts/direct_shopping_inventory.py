"""Read-only live inventory of DQ200 shopping ads and campaign settings."""
import json, os, urllib.request
from pathlib import Path
TOKEN=os.environ["YANDEX_DIRECT_TOKEN"]
HEAD={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"): HEAD["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
def get(endpoint,params):
 req=urllib.request.Request("https://api.direct.yandex.com/json/v5/"+endpoint,data=json.dumps({"method":"get","params":params}).encode(),headers=HEAD)
 with urllib.request.urlopen(req,timeout=90) as res: body=json.load(res)
 if body.get("error"):raise RuntimeError(json.dumps(body["error"],ensure_ascii=False))
 return body.get("result",{})
cid=716316525
ads=get("ads",{"SelectionCriteria":{"CampaignIds":[cid]},"FieldNames":["Id","CampaignId","AdGroupId","Type","Status","State"],"Page":{"Limit":1000}})
groups=get("adgroups",{"SelectionCriteria":{"CampaignIds":[cid]},"FieldNames":["Id","CampaignId","Name","Status","RegionIds"],"Page":{"Limit":1000}})
out={"campaign_id":cid,"ads":ads.get("Ads",[]),"groups":groups.get("AdGroups",[]),"note":"Read-only snapshot. Shopping ad text can originate from product feed; do not edit product titles without source mapping."}
Path("data/direct_live_shopping_inventory.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("Ads:",len(out["ads"]),"groups:",len(out["groups"]))
