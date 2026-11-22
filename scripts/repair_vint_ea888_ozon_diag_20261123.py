"""Read-only diagnosis of Ozon EA888 intake-manifold screw wrong type.
Scope: vint-kollektora-ea888 only; check exact product, category/type tree.
"""
import json,sys,re,traceback
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client
TARGET="vint-kollektora-ea888"
SAMPLES=["01X301127C","WHT001922","0am325025f","EA888","Ea888gen3"]
OUT=ROOT/"data/repair_vint_ea888_ozon_diag_20261123.json"
def save(k,x):
    try:d=json.loads(OUT.read_text())
    except (FileNotFoundError,ValueError):d={}
    d["timestamp_utc"]=datetime.now(timezone.utc).isoformat()
    d[k]=x
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"step":k,"summary":({"error_type":x.get("error_type")} if isinstance(x,dict) and x.get("error_type") else {"matches":len(x) if isinstance(x,list) else len(x.keys()) if isinstance(x,dict) else None})},ensure_ascii=False),flush=True)
def safe(k,fn):
    try:save(k,fn())
    except Exception as e:
        val={"error_type":type(e).__name__,"summary":str(e)[:450]}
        save(k,val)
def attributes():
    rows=ozon_client.get_product_names(offer_ids=[TARGET]+SAMPLES,limit=30)
    p=[]
    for x in rows:
        q={k:x.get(k) for k in ("id","offer_id","name","description_category_id","type_id","visibility","weight","depth","width","height","dimension_unit","weight_unit","barcode","primary_image")}
        q["attributes"]=[{"id":z.get("id"),"value":[v.get("value") for v in (z.get("values") or [])]}
            for z in (x.get("attributes") or [])]
        p.append(q)
    return p
def info():
    rows=ozon_client.get_product_info_list([TARGET])
    return [{k:v for k,v in x.items() if k in ("offer_id","product_id","id","name","description_category_id","type_id","statuses","status","errors","visibility","is_archived","archived","_error")} for x in rows]
def tree():
    try:t=ozon_client.get_category_tree("RU")
    except Exception as exc:
        # Current Ozon has deprecated the old /v2/category/tree in some accounts.
        reply=ozon_client._post("/v1/description-category/tree",{"language":"RU"})
        t=reply.get("result") or []
    pending=[(t,[])]
    candidates=[]
    searched=0
    pattern=re.compile(r"винт|болт|креп[её]ж|метиз|крепител|впуск|двигател|моторн|резьбов",re.I)
    while pending:
        node,path=pending.pop()
        if isinstance(node,list):
            pending.extend((x,path) for x in node)
            continue
        if not isinstance(node,dict):continue
        searched+=1
        name=str(node.get("name") or node.get("category_name") or "")
        here=path+[name]
        children=node.get("children") or node.get("types") or []
        if pattern.search(name):
            candidates.append({"path":here,"node":{k:v for k,v in node.items()
                if k not in ("children","types","attributes","description")}})
        pending.append((children,here))
    return {"searched_nodes":searched,"candidates":candidates[:120]}
def category_details():
    rows=ozon_client.get_product_names(offer_ids=[TARGET],limit=30)
    if len(rows)!=1:return {"status":"product_not_returned"}
    c=rows[0].get("description_category_id")
    t=rows[0].get("type_id")
    if not c or not t:return {"status":"no_category_type","category":c,"type":t}
    attrs=ozon_client.get_category_attributes(c,t)
    return {"category":c,"type":t,"required_attributes":[{"id":v.get("id"),"name":v.get("name"),"is_required":v.get("is_required")}
         for v in attrs if v.get("is_required")]}
if __name__=="__main__":
    req=json.loads((ROOT/"data/repair_vint_ea888_ozon_diag_20261123_request.json").read_text())
    if req.get("sku")!=TARGET or req.get("write_enabled") is not False:raise ValueError("Wrong scope")
    for k,fn in [("target_and_samples",attributes),("target_moderation",info),("category_tree",tree),("current_category_required",category_details)]:
        safe(k,fn)
