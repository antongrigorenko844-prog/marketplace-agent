"""Read-only: find exact existing EA888 Gen3+valve kit, proper new SKU,
category examples, and existing photos across Ozon/WB/YM before creating new kit."""
import json,re,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SKU="EA888gen3-filter-kit"
OUT=ROOT/"data/ea888_gen3_filter_kit_preflight_20261123.json"
def emit(k,v):
    try:o=json.loads(OUT.read_text())
    except (OSError,ValueError):o={}
    o["at"]=datetime.now(timezone.utc).isoformat();o[k]=v
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({k:v},ensure_ascii=False,default=str),flush=True)
def inspect():
    import ozon_client,wb_client,yandex_market_client as ym
    sample=["EA888gen3","EA888","Ea888gen3",SKU]
    try:
        oz=ozon_client.get_product_names(offer_ids=sample,limit=100)
        d=[]
        for c in oz:
            z={key:c.get(key) for key in ("offer_id","name","description_category_id","type_id","weight","depth","width","height","dimension_unit","weight_unit","images","primary_image")}
            z["attributes"]=[{"id":a.get("id"),"values":[str(v.get("value"))[:120] for v in (a.get("values") or [])[:2]]} for a in (c.get("attributes") or [])]
            d.append(z)
        emit("ozon",{"status":"ok","sample_rows":d})
    except Exception as e:emit("ozon",{"status":"error","type":type(e).__name__})
    try:
        rows=wb_client.list_cards(limit=100)
        a=[{"vendorCode":z.get("vendorCode"),"nmID":z.get("nmID"),
           "title":z.get("title"),"subjectID":z.get("subjectID"),"subjectName":z.get("subjectName"),
           "dimensions":z.get("dimensions"),"brand":z.get("brand"),
           "characteristics":z.get("characteristics")}
           for z in rows if re.search("ea888|масляного фильтра|корпус.*фильтра|масляный фильтр",str(z.get("vendorCode"))+" "+str(z.get("title")),re.I)]
        objs={}
        for q in ["масляные фильтры","корпуса фильтров","фильтры масляные","клапаны","Ремкомплекты"]:
            try:objs[q]=[{"subjectID":z.get("subjectID"),"name":z.get("subjectName") or z.get("name")}
                        for z in wb_client.get_subjects(name=q)[:12]]
            except Exception as e:objs[q]={"error":type(e).__name__}
        emit("wb",{"status":"ok","count":len(rows),"matches":a[:40],"categories":objs})
    except Exception as e:emit("wb",{"status":"error","type":type(e).__name__})
    try:
        a=ym.get_all_offers(archived=False)
        emit("market",{"status":"ok","matches":[{k:z.get(k) for k in ("sku","name","archived","market_sku","category")}
            for z in a if z.get("sku") in sample]})
    except Exception as e:emit("market",{"status":"error","type":type(e).__name__})
if __name__=="__main__":inspect()
