"""Read-only category discovery for EXACTLY two DSG workshop tool products.
Print and save no secret values. No creation, price/stock updates, or imports.
"""
import datetime as dt
import json, os, re, time
from pathlib import Path
from urllib.parse import urlsplit
import requests

OUT=Path("data/dsg_tools_categories_20261120.json")
TOKENS=("инструмент", "автосервис", "автомобил", "диагност", "манометр", "опрессов", "оборудован", "измерител", "пневмат", "тестер", "давлен")
STRONG=("специнструмент", "автоинструмент", "манометр", "диагностическ", "набор инструмент", "для автосервиса", "пневмотестер", "проверки давления")
report={"date_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"mode":"read_only","products":["tool-dq200-acc-restore","tool-dsg-pressure-hp"],"categories":{},"network_calls":[]}
_orig=requests.sessions.Session.request
def readonly(self, method, url, **kw):
    u=urlsplit(url); method=method.upper()
    allowed=(u.scheme=="https" and
         ((u.hostname=="api-seller.ozon.ru" and method=="POST" and u.path in ("/v1/description-category/tree","/v1/description-category/attribute"))
          or (u.hostname=="content-api.wildberries.ru" and method=="GET" and u.path in ("/content/v2/object/all","/content/v2/object/charcs/0") )
          or (u.hostname=="content-api.wildberries.ru" and method=="GET" and re.fullmatch(r"/content/v2/object/charcs/[0-9]+",u.path))))
    if not allowed: raise RuntimeError("Only allowlisted read category requests")
    kw["timeout"]=25
    kw["allow_redirects"]=False
    event={"provider":u.hostname,"path":u.path,"method":method}
    report["network_calls"].append(event)
    try:
        response=_orig(self, method, url, **kw)
        event["http_status"]=response.status_code
        return response
    except Exception as e:
        event["error_type"]=type(e).__name__;raise
requests.sessions.Session.request=readonly
def compact_error(ex):
    return {"error_type":type(ex).__name__,"message":re.sub(r"[^a-zA-Z0-9 _\-:.]","?",str(ex))[:240]}
def rank(path):
    s=path.lower().replace("ё","е")
    return 6*sum(t in s for t in STRONG)+sum(t in s for t in TOKENS)
def walk(nodes, parent=()):
    if isinstance(nodes,dict):nodes=[nodes]
    if not isinstance(nodes,list):return
    for n in nodes:
        if not isinstance(n,dict):continue
        name=str(n.get("category_name") or n.get("type_name") or n.get("name") or n.get("subjectName") or "")
        parts=(*parent,name) if name else parent
        kids=n.get("children") or []
        if kids:yield from walk(kids,parts)
        else:
            path=" / ".join(parts)
            if rank(path)>1 and ("авто" in path.lower() or "инструмент" in path.lower() or "манометр" in path.lower() or "измерител" in path.lower() or "пневмат" in path.lower() or "тестер" in path.lower()):
                yield {"path":path,"rank":rank(path),"description_category_id":n.get("description_category_id"),"type_id":n.get("type_id"),"disabled":n.get("disabled")}
def probe_ozon():
    cid=os.getenv("OZON_CLIENT_ID"); key=os.getenv("OZON_API_KEY")
    if not (cid and key):return {"status":"missing_secret"}
    h={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
    u="https://api-seller.ozon.ru/v1/description-category/tree"
    response=requests.post(u,headers=h,json={"language":"RU"})
    if not response.ok:return {"status":"http_error","http_status":response.status_code,"error_preview":response.text[:240]}
    tree=response.json().get("result") or []
    matches=sorted(walk(tree),key=lambda x:-x["rank"])
    unique={}
    for e in matches:
        key=(e["description_category_id"],e["type_id"])
        if key not in unique:unique[key]=e
    candidates=list(unique.values())
    chosen=[e for e in candidates if e["description_category_id"] and e["type_id"] and not e["disabled"]][:10]
    details=[]
    for c in chosen:
        try:
            res=requests.post("https://api-seller.ozon.ru/v1/description-category/attribute",headers=h,json={"description_category_id":c["description_category_id"],"type_id":c["type_id"],"language":"RU"})
            if not res.ok: details.append({"path":c["path"],"http_status":res.status_code});continue
            raw=res.json().get("result") or []
            details.append({"path":c["path"],"description_category_id":c["description_category_id"],"type_id":c["type_id"],
              "attributes":[{"id":a.get("id"),"name":a.get("name"),"required":a.get("is_required"),"dictionary":a.get("dictionary_id"),"type":a.get("type")} for a in raw if a.get("is_required") or a.get("id") in (85,8229,9048,22232)]})
        except Exception as ex:details.append({"path":c["path"],**compact_error(ex)})
        time.sleep(0.25)
    return {"status":"ok","tree_top_level":len(tree),"candidate_count":len(candidates),"candidates":candidates[:65],"attribute_samples":details}
def probe_wb():
    token=os.getenv("WB_API_TOKEN")
    if not token:return {"status":"missing_secret"}
    hdr={"Authorization":token,"Content-Type":"application/json"}
    queries=["автоинструмент","манометр","диагностическое","измерительные","набор инструментов","инструменты","пневмо"]
    searches=[]
    for name in queries:
        try:
            res=requests.get("https://content-api.wildberries.ru/content/v2/object/all",headers=hdr,params={"name":name})
            if not res.ok: searches.append({"name":name,"http_status":res.status_code});continue
            items=res.json().get("data") or []
            searches.append({"name":name,"matches":[{k:v for k,v in s.items() if k in ("subjectID","subjectName","parentID","parentName","name")} for s in items[:50]],"total_count":len(items)})
        except Exception as e:searches.append({"name":name,**compact_error(e)})
        time.sleep(0.4)
    return {"status":"ok","searches":searches}
try:report["categories"]["ozon"]=probe_ozon()
except Exception as e:report["categories"]["ozon"]=compact_error(e)
try:report["categories"]["wildberries"]=probe_wb()
except Exception as e:report["categories"]["wildberries"]=compact_error(e)
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print("Read-only categories report:",OUT)
print("Ozon:",report["categories"]["ozon"].get("status"),"WB:",report["categories"]["wildberries"].get("status"))
