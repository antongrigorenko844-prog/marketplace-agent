"""Read-only campaign goal/strategy audit via Yandex Direct API."""
import json,os,urllib.request
from pathlib import Path
headers={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
url="https://api.direct.yandex.com/json/v5/campaigns"
params={"SelectionCriteria":{"Ids":[716316525]},"FieldNames":["Id","Name","Type","State","Status"],"TextCampaignFieldNames":["BiddingStrategy","CounterIds","Settings"]}
req=urllib.request.Request(url,data=json.dumps({"method":"get","params":params}).encode(),headers=headers)
with urllib.request.urlopen(req,timeout=90) as res:body=json.load(res)
if body.get("error"):raise RuntimeError(json.dumps(body["error"],ensure_ascii=False))
Path("data/direct_campaign_goals_audit.json").write_text(json.dumps(body.get("result",{}),ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(body.get("result",{}),ensure_ascii=False)[:6000])
