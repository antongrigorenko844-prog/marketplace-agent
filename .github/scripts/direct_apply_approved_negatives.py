"""Apply six owner-approved, narrowly scoped negatives to Leoparts' active DQ200 EPK.
Preserves all existing campaign negatives and verifies the API result.
"""
import json, os, urllib.request, urllib.error
from pathlib import Path

CAMPAIGN_ID = 716316525
EXPECTED_NAME = "Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
NEW = [
    "купить коробку дсг 7 dq200",
    "мерседес мехатроник",
    "кпп dsg что это такое",
    "купить дсг 7 dq200 новую",
    "дсг 250 коробка что это",
    "куда подходит мехатроник dq200",
]
API = "https://api.direct.yandex.com/json/v5/campaigns"
HEADERS = {"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],
           "Content-Type":"application/json; charset=utf-8", "Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):
    HEADERS["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]

def call(method, params):
    body = json.dumps({"method":method,"params":params}).encode()
    req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            response = json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError("API HTTP "+str(e.code)+": "+e.read().decode(errors="replace")[:800])
    if response.get("error"):
        raise RuntimeError("API "+json.dumps(response["error"],ensure_ascii=False))
    return response.get("result",{})

def read():
    rows = call("get",{"SelectionCriteria":{"Ids":[CAMPAIGN_ID]},
                        "FieldNames":["Id","Name","Type","State","NegativeKeywords"]}).get("Campaigns",[])
    if len(rows)!=1 or rows[0].get("Id")!=CAMPAIGN_ID or rows[0].get("Name")!=EXPECTED_NAME:
        raise RuntimeError("Campaign identity mismatch, refusing to modify")
    if rows[0].get("Type") not in ("UNIFIED_CAMPAIGN","TEXT_CAMPAIGN") or rows[0].get("State")!="ON":
        raise RuntimeError("Campaign type/state not matched: "+repr({"Type":rows[0].get("Type"),"State":rows[0].get("State"),"Status":rows[0].get("Status")})+"; refusing to modify")
    return rows[0]

campaign = read()
existing_obj = campaign.get("NegativeKeywords")
if existing_obj is None:
    existing = []
elif isinstance(existing_obj,dict) and isinstance(existing_obj.get("Items"),list):
    existing = existing_obj["Items"]
else:
    raise RuntimeError("Unexpected existing negative keywords structure: no changes")
if not all(isinstance(x,str) for x in existing):
    raise RuntimeError("Unexpected negative keyword value")
combined = list(existing)
seen = {x.casefold().strip() for x in existing}
for term in NEW:
    if term.casefold() not in seen:
        combined.append(term)
        seen.add(term.casefold())
if len("".join(combined))>20000:
    raise RuntimeError("Negative keywords too long")
if combined != existing:
    result=call("update",{"Campaigns":[{"Id":CAMPAIGN_ID,"NegativeKeywords":{"Items":combined}}]})
    items=result.get("UpdateResults",[])
    if len(items)!=1 or items[0].get("Errors"):
        raise RuntimeError("Update failed: "+json.dumps(result,ensure_ascii=False))
verified=read()
after=verified.get("NegativeKeywords")
if not isinstance(after,dict) or not isinstance(after.get("Items"),list):
    raise RuntimeError("Cannot verify negatives after update")
actual={x.casefold().strip().replace("!", "") for x in after["Items"]}
if not set(x.casefold().replace("!", "") for x in combined).issubset(actual):
    raise RuntimeError("Verification mismatch: missing="+repr(sorted(set(x.casefold() for x in combined)-actual))+"; existing_count="+str(len(existing))+"; actual_count="+str(len(after["Items"]))+"; actual="+repr(after["Items"][:100]))
output=Path("data/direct_negatives_apply_result.txt")
output.write_text("Campaign: "+EXPECTED_NAME+"\nID: "+str(CAMPAIGN_ID)+
    "\nExisting negatives preserved: "+str(len(existing))+
    "\nApproved phrases present: "+str(len(NEW))+
    "\nTotal negatives after: "+str(len(after["Items"]))+
    "\nVerified: YES\n"+"\n".join(NEW)+"\n",encoding="utf-8")
print(output.read_text())
