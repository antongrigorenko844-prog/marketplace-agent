"""Avito paid autoload read-only diagnostic: v4 upload feedback, actual ads, category fields."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
OUTPUT=ROOT/"data/avito_paid_category_health.json"
RELEVANT=("инструмент","автосервис","оборудован","трансмис","двигател","запчаст","диагност","ремонт")
STATUSES=("active","old","blocked","rejected")
SKUS=("6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2","tool-dq200-acc-restore","tool-dsg-pressure-hp")

def api(path,**kwargs):
    try:
        return av._request("GET",path,retries=1,**kwargs),None
    except Exception as e:
        return {},str(e)[:320]

def compact(item):
    if not isinstance(item,dict): return {}
    return {k:item[k] for k in ("id","ad_id","avito_id","url","title","status","section","messages","price","is_active") if k in item}

def tree_nodes(value,trail=(),depth=0):
    if depth>20: return []
    result=[]
    if isinstance(value,dict):
        label=next((str(value[k]) for k in ("title","name","label","text") if isinstance(value.get(k),str)),None)
        now=trail+(label,) if label else trail
        slug=value.get("slug") or value.get("node_slug")
        if slug and (any(w in " ".join(now).lower() for w in RELEVANT) or
                     any(w in str(slug).lower() for w in RELEVANT)):
            result.append({"path":" / ".join(now)[-270:],"slug":str(slug),"id":value.get("id")})
        for k,v in value.items():
            if k in ("fields","examples","dictionary","values"):continue
            if isinstance(v,(dict,list)):
                result+=tree_nodes(v,now,depth+1)
    elif isinstance(value,list):
        for x in value:
            result+=tree_nodes(x,trail,depth+1)
    return result

def main():
    report={"checked_at_utc":datetime.now(timezone.utc).isoformat(),"mode":"read_only",
            "seller_writes":0,"sku_targets":SKUS}
    tree,err=api("/autoload/v1/user-docs/tree")
    report["category_api_error"]=err
    nodes=tree_nodes(tree)
    unique={x["slug"]:x for x in nodes}
    # Show only automotive business equipment and engine/vehicle part branches;
    # do not select every child of generic 'tools' parents (e.g. musical instruments).
    exact=[x for x in unique.values() if (
        "Для автобизнеса" in x["path"] or
        ("Запчасти и аксессуары" in x["path"] and
         any(k in x["path"] for k in ("Двигател", "Инструмент", "Для автомобилей"))))]
    report["category_matches"]=exact[:180]
    report["total_matching_slugs"]=len(unique)
    report["automotive_matching_slugs"]=len(exact)
    # Only a category's exact leaf can supply its required field schema.
    choose=[x for x in exact if (
        "Для автобизнеса / Диагностика и ремонт / " in x["path"] or
        "Для автобизнеса / Заправочное / Заправка масел" in x["path"] or
        "Инструменты" in x["path"].split(" / ")[-1] or
        "Двигатель" in x["path"].split(" / ")[-1])]
    priority_slugs=("diagnosticheskii_i_izmeritelnyi_instrument","ruchnoi_instrument_2304150","osnastka_i_raskhodnye_materialy","dvigatel")
    prioritized=[x for slug in priority_slugs for x in exact if x["slug"]==slug]
    choose=prioritized+[x for x in choose if x["slug"] not in priority_slugs]
    fields=[]
    for x in choose[:8]:
        data,error=api("/autoload/v1/user-docs/node/"+x["slug"]+"/fields")
        compact_fields=[]
        for item in data.get("fields") or []:
            meta=(item.get("content") or [{}])[0]
            compact_fields.append({"tag":item.get("tag"),"label":item.get("label"),
                      "required":meta.get("required"),
                      "field_type":meta.get("field_type"),
                      "allowed_values":[v.get("value") for v in (meta.get("values") or [])[:18]]})
        fields.append({"slug":x["slug"],"path":x["path"],
                       "node":data.get("node"),"fields":compact_fields,"error":error})
    report["candidate_category_fields"]=fields
    current,e=api("/autoload/v4/uploads/current")
    report["current_upload"]={"error":e,
                              "id":current.get("upload_id"),"status":current.get("status"),
                              "started_at":current.get("started_at"),
                              "section_stats":current.get("section_stats")}
    last,e=api("/autoload/v4/uploads/last_successful")
    report["last_successful_upload"]={"error":e,
                                    "id":last.get("upload_id"),"status":last.get("status"),
                                    "started_at":last.get("started_at"),
                                    "section_stats":last.get("section_stats")}
    listing={}
    status_by_id={}
    for status in STATUSES:
        try:
            rows=av.list_all_items(statuses=[status],per_page=100,max_pages=8)
            for row in rows:
                if row.get("id") is not None:
                    status_by_id[str(row["id"])]=status
            listing[status]={"count":len(rows),
                             "examples":list(map(compact,rows[:8]))}
        except Exception as ex:
            listing[status]={"error":str(ex)[:320]}
    report["seller_listings_by_status"]=listing
    previous=ROOT/"data/avito_autoload.json"
    if previous.is_file():
        prior=json.loads(previous.read_text(encoding="utf-8"))
        expired=[x for x in prior.get("items") or [] if
                   (x.get("section") or {}).get("slug")=="stopped_by_expiration"]
        report["previously_expired_reactivation"]=[
            {"sku":x.get("ad_id"),"avito_id":x.get("avito_id"),
             "current_status":status_by_id.get(str(x.get("avito_id")),"unknown")}
            for x in expired]

    # v4 current and last_successful item-level failures, with current publication state
    for label,path in [("current","/autoload/v4/uploads/current/items"),
                       ("last_successful","/autoload/v4/uploads/last_successful/items")]:
        error=None
        rows=[]
        for page in range(1,6):
            response,error=api(path,params={"page":page,"perPage":50})
            if error: break
            batch=response.get("items") or response.get("resources") or []
            if not isinstance(batch,list):break
            rows.extend(batch)
            if len(batch)<50:break
        bad=[x for x in rows if any(k in json.dumps(x,ensure_ascii=False).lower() for k in
             ("ошибка","заблокировано","истёк срок","повторным","не удалось"))]
        matched=[x for x in rows if str(x.get("ad_id") or "").lower() in SKUS]
        report[label+"_items"]={"error":error,"count":len(rows),
                                  "possible_problems":[compact(x) for x in bad[:30]],
                                  "five_approved_matches":[compact(x) for x in matched]}
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_HEALTH",json.dumps({"upload":report["current_upload"],"slugs":len(unique),
        "listing_counts":{k:v.get("count") for k,v in listing.items()},
        "five":report["current_items"].get("five_approved_matches")},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
