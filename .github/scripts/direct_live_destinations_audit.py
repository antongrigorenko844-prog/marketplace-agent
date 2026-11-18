"""Read-only check of actual live ad destination URLs for Leoparts campaign."""
import json,os,urllib.request
from pathlib import Path
headers={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
url="https://api.direct.yandex.com/json/v5/ads"
records=[]
page=0
while True:
    params={"SelectionCriteria":{"CampaignIds":[716316525]},"FieldNames":["Id","CampaignId","AdGroupId","State","Status","Type"],"TextAdFieldNames":["Href","Title"],"Page":{"Limit":1000,"Offset":page*1000}}
    req=urllib.request.Request(url,data=json.dumps({"method":"get","params":params}).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=90) as res:body=json.load(res)
    if body.get("error"):raise RuntimeError(json.dumps(body["error"],ensure_ascii=False))
    result=body.get("result",{})
    records.extend(result.get("Ads",[]))
    if not result.get("LimitedBy"):break
    page+=1
    if page>20:raise RuntimeError("Unexpected pagination")
out=Path("data/direct_live_ad_destinations.json")
out.write_text(json.dumps({"campaign_id":716316525,"count":len(records),"ads":records},ensure_ascii=False,indent=2),encoding="utf-8")
print("Read-only destination audit, ads:",len(records))
for x in records:
    print(x.get("Id"),x.get("State"),x.get("Status"),x.get("Type"),x.get("TextAd",{}).get("Href","(non-text)"))
