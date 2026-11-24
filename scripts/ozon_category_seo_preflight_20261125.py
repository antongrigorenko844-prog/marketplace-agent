"""Read-only live category preflight for exact Ozon SEO-audit cohort.
No product import, price, stock, photo, description, or other seller mutation."""
import sys,json,datetime,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ozon_client as oz
OUT=ROOT/"data/ozon_category_seo_preflight_20261125.json"
TARGETS=["0cw0am","0AM325443D","0am325025HX","0cw325025h","porshni-usil-pylniki-kryshki","0am325091f-set","porshni-usil-pylniki-kryshki-v2","vtulki-shtokov-2sht","EA888","0AM927769N","tolkateli-salniki-alum-komplekt","EA888gen3","0am325091e-set","55565480","0cw0amdsgdq200","Dq200dsg7","0am325477ae","korpus-filtra-0bh325159","WHT001922","remkomplekt-shtokov-orig","Dq200dsg70am0cw","plita-filtr-salniki-orig","plita-filtr-pylniki-orig","salnikvhod","salnikprivod-r","salnikprivod-l","pylniki-shtokov-orig","0cw0am325025","datchik-peredach-dq200","gidroakkumulyator-orig","0am325025u","0am325025z","filtr-dsg7-dq200","0AM927769F","plita-filtr-orig","podshipnik-vilki-6","datchik-scepleniya-dq200","chip-02e927770","derzhatel-datchikov-dq200"]
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
r={"mode":"read_only","external_mutations":0,"status":"started","started":now(),"targets":TARGETS}
def save():
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n")
def read(label,fn):
    try:return fn()
    except Exception as e:
        r.setdefault("errors",{})[label]={"type":type(e).__name__,"error":str(e)[:220]}
        save()
        return None
save()
tree=read("tree",lambda:oz.get_category_tree("RU")) or []
types={}
def walk(nodes,path=(),cat=None):
    for n in nodes:
        if not isinstance(n,dict):continue
        name=n.get("category_name") or n.get("type_name") or n.get("name") or ""
        q=path+(name,)
        c=n.get("description_category_id") or cat
        if n.get("type_id") and c:
            types[(int(c),int(n["type_id"]))]={"category_id":int(c),"type_id":int(n["type_id"]),"path":" / ".join(q),"disabled":bool(n.get("disabled"))}
        walk(n.get("children") or n.get("types") or [],q,c)
walk(tree)
r["type_count"]=len(types)
r["candidate_types"]=[v for v in types.values() if not v["disabled"] and ("авто" in v["path"].lower() or "транспорт" in v["path"].lower()) and any(s in v["path"].lower() for s in ("трансмисс","коробк","прокладк","сальник","маслян","маслен","смазк","крепеж","крепёж","метиз","распредвал","импульс","фильтр","мехатрон","гидроблок","акпп","датчик"))]
save()
catalog=read("product_attributes",lambda:oz.get_product_names(limit=100)) or []
if not catalog:
    catalog=read("target_attributes",lambda:oz.get_product_names(offer_ids=TARGETS,limit=100)) or []
def summarize(x):
    c=int(x.get("description_category_id") or 0);t=int(x.get("type_id") or 0)
    return {"offer_id":x.get("offer_id"),"product_id":x.get("id"),"name":x.get("name"),"category_id":c,"type_id":t,"path":types.get((c,t),{}).get("path"),"attribute_ids":[a.get("id") for a in x.get("attributes",[])],"attribute_values":[{"id":a.get("id"),"values":a.get("values")} for a in x.get("attributes",[]) if a.get("id") in (85,8229,22232,23536,7202,7324,11031)],"has_complex_attributes":bool(x.get("complex_attributes")),"has_images":bool(x.get("primary_image") or x.get("images"))}
r["live_catalog"]=[summarize(x) for x in catalog if x.get("offer_id")]
r["matched_targets"]=[x for x in r["live_catalog"] if x["offer_id"] in TARGETS]
r["missing_targets"]=[x for x in TARGETS if x not in {z["offer_id"] for z in r["matched_targets"]}]
r["status"]="complete" if not r["missing_targets"] and not r.get("errors") else "partial"
r["finished"]=now()
save()
print(json.dumps({"status":r["status"],"matches":len(r["matched_targets"]),"candidate_types":len(r["candidate_types"]),"missing":r["missing_targets"]},ensure_ascii=False))
