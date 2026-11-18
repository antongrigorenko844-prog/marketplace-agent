"""Rollback only the 34 broad negatives added in the second batch.
Preserve original 18 and first 6, verify by re-reading Yandex Direct.
"""
import json,os,urllib.request
from pathlib import Path
ID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
REMOVE=[
    "купить коробку dsg7",
    "купить коробку dsg 7",
    "купить коробку дсг7",
    "купить коробку дсг 7",
    "купить кпп dq200",
    "купить кпп дк200",
    "купить коробку dq200",
    "купить коробку дк200",
    "коробка dsg7 в сборе",
    "коробка dsg 7 в сборе",
    "коробка дсг7 в сборе",
    "коробка дсг 7 в сборе",
    "коробка dq200 в сборе",
    "коробка дк200 в сборе",
    "коробка передач целиком",
    "кпп dq200 в сборе",
    "кпп дк200 в сборе",
    "контрактная коробка dq200",
    "контрактная кпп dsg7",
    "коробка dsg7 бу",
    "коробка дсг7 бу",
    "сколько стоит коробка дсг7",
    "сколько стоит коробка dsg7",
    "цена коробки dq200",
    "стоимость кпп dsg7",
    "замена коробки dsg7 цена",
    "ремонт кпп dq200 под ключ",
    "что такое коробка dsg",
    "что такое коробка дсг",
    "коробка dsg что это такое",
    "коробка дсг что это такое",
    "на какие машины подходит мехатроник",
    "на какие автомобили подходит мехатроник",
    "куда ставится мехатроник"
]
HEADERS={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],"Content-Type":"application/json; charset=utf-8","Accept-Language":"ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"): HEADERS["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
def call(method,params):
    req=urllib.request.Request("https://api.direct.yandex.com/json/v5/campaigns",data=json.dumps({"method":method,"params":params}).encode(),headers=HEADERS)
    with urllib.request.urlopen(req,timeout=90) as r: result=json.load(r)
    if result.get("error"): raise RuntimeError(str(result["error"]))
    return result["result"]
def get():
    rows=call("get",{"SelectionCriteria":{"Ids":[ID]},"FieldNames":["Id","Name","Type","State","NegativeKeywords"]}).get("Campaigns",[])
    if len(rows)!=1 or rows[0]["Name"]!=NAME or rows[0]["State"]!="ON":raise RuntimeError("Campaign identity/state mismatch")
    return rows[0]["NegativeKeywords"]["Items"]
def norm(s):return s.casefold().replace("!","").strip()
before=get()
remove={norm(x) for x in REMOVE}
kept=[x for x in before if norm(x) not in remove]
if len(kept)<24: raise RuntimeError("Safety: expected at least 24 preserved negatives, got "+str(len(kept)))
if len(kept)!=len(before):
    result=call("update",{"Campaigns":[{"Id":ID,"NegativeKeywords":{"Items":kept}}]})
    updates=result.get("UpdateResults",[])
    if len(updates)!=1 or updates[0].get("Errors"):raise RuntimeError(str(result))
after=get()
if {norm(x) for x in after}!={norm(x) for x in kept}:raise RuntimeError("Verification failed")
if any(norm(x) in remove for x in after):raise RuntimeError("Unsafe broad negatives still present")
out=Path("data/direct_negatives_safety_rollback.txt")
out.write_text(f"Verified: YES\\nBefore: {len(before)}\\nAfter: {len(after)}\\nRemoved: {len(before)-len(after)}\\nPreserved: {len(after)}\\n",encoding="utf-8")
print(out.read_text())
