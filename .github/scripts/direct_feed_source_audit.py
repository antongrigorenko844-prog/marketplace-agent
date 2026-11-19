import json,os,urllib.request,urllib.error
from pathlib import Path
h={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
url="https://api.direct.yandex.com/json/v5/feeds"
payload={"method":"get","params":{"SelectionCriteria":{"Ids":[4061297]},"FieldNames":["Id","Name","SourceType","UpdatedAt","CampaignIds","NumberOfItems","NumberOfListings","Status"],"Page":{"Limit":100}}}
req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers=h)
try:
 with urllib.request.urlopen(req,timeout=90) as res:out=json.load(res)
except urllib.error.HTTPError as e:out={"http_error":e.code,"body":e.read().decode(errors="replace")[:2000]}
Path("data/direct_feed_4061297_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False)[:4000])
