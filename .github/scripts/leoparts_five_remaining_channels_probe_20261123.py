"""Read-only Ozon/Avito evidence for five approved LEOPARTS products.
Zero seller writes, no price/stock changes, no import attempts.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import ozon_client
import avito_client

MANIFEST = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"
OUTPUT = ROOT / "data/leoparts_five_remaining_channels_probe_20261123.json"
PAT = re.compile(r"6t30|6t40|6t45|6t50|соленоид|мехатрон|набор.{0,20}инструмент|специнструмент|гидроакк|манометр|измерен.{0,20}давлен|провер.{0,20}давлен|тестер|диагност|блок.{0,15}управлен|6т30|6т40", re.I)

def summary_attrs(row):
    attrs=[]
    for attr in row.get("attributes") or []:
        k = attr.get("id") or attr.get("attribute_id")
        values = attr.get("values") or []
        # No copied description/SKU of samples; only labels needed to assess category.
        v = [str(x.get("value") or x.get("dictionary_value_id") or "")[:65] for x in values[:2] if isinstance(x,dict)]
        attrs.append({"id": k, "name": str(attr.get("name") or "")[:75], "value":v})
    return attrs[:120]

def safe_error(exc):
    return {"error_type":type(exc).__name__,"description":str(exc).split("Client-Id")[0][:150]}

def run():
    items=json.loads(MANIFEST.read_text(encoding="utf8"))["items"]
    skus={x["sku"] for x in items}
    report={"mode":"read_only","checked_utc":datetime.now(timezone.utc).isoformat(),
            "sku_count":len(items),"planned_external_writes":0,"ozon":{},"avito":{}}
    try:
        rows=ozon_client.get_product_names(limit=200)
        seen=[x for x in rows if str(x.get("offer_id") or "") in skus]
        similar=[]
        for row in rows:
            sku=str(row.get("offer_id") or "")
            title=str(row.get("name") or "")
            if PAT.search(title+" "+sku):
                similar.append({"sku":sku,"name":title[:145],
                    "description_category_id":row.get("description_category_id"),
                    "type_id":row.get("type_id"),
                    "attributes":summary_attrs(row),
                    "depth":row.get("depth"),"width":row.get("width"),
                    "height":row.get("height"),"weight":row.get("weight")})
        report["ozon"]={"total_catalog":len(rows),"target_found":[x.get("offer_id") for x in seen],
                        "candidate_samples":similar[:75],"candidate_sample_count":len(similar)}
        # Optional Ozon category metadata: only read permitted seller categories, never guess values.
        try:
            sample_ids=sorted(set((r.get("description_category_id"),r.get("type_id")) for r in similar
                                  if r.get("description_category_id") and r.get("type_id")))[:12]
            category_attrs=[]
            for cat,typ in sample_ids:
                try:
                    data=ozon_client._post("/v1/description-category/attribute",
                        {"description_category_id":cat,"type_id":typ,"language":"DEFAULT"}, retries=1)
                    a=data.get("result") or data.get("attributes") or []
                    if isinstance(a,dict): a=a.get("attributes") or []
                    category_attrs.append({"category":cat,"type":typ,
                        "required":[{"id":x.get("id"),"name":x.get("name"),"is_required":x.get("is_required")}
                                    for x in a if isinstance(x,dict) and x.get("is_required")][:50]})
                except Exception as exc:
                    category_attrs.append({"category":cat,"type":typ,"error_type":type(exc).__name__})
            report["ozon"]["sample_category_required_attrs"]=category_attrs
        except Exception as exc:
            report["ozon"]["attributes_error"]=safe_error(exc)
    except Exception as exc:
        report["ozon"]=safe_error(exc)
    try:
        ads=avito_client.list_all_items(max_pages=20)
        report["avito"]["total_returned"]=len(ads)
        matching=[]
        for a in ads:
            title=str(a.get("title") or a.get("name") or "")
            maybe_sku=str(a.get("id") or a.get("idOfAdvert") or a.get("externalId") or "")
            if PAT.search(title) or any(s in maybe_sku for s in skus):
                matching.append({"id":a.get("id"),"title":title[:145],
                                 "status":a.get("status"),"category":a.get("category")})
        report["avito"]["related_ads"]=matching[:70]
    except Exception as exc:
        report["avito"]["ads_access"]=safe_error(exc)
    try:
        d=avito_client.get_autoload_report_items(max_pages=5,per_page=100)
        head=d.get("report") or {}
        report["avito"]["autoload_last"]={k:v for k,v in head.items() if k in ("id","report_id","status","completed_at","created_at","started_at","time","message","errors")}
        entries=d.get("items") or []
        report["avito"]["autoload_rows_returned"]=len(entries)
        report["avito"]["autoload_target_rows"]=[{
            "id":x.get("id"),"external_id":x.get("external_id") or x.get("externalId"),
            "status":x.get("status"),"errors":str(x.get("errors") or "")[:350]
        } for x in entries if any(s in json.dumps(x,ensure_ascii=False) for s in skus)][:10]
    except Exception as exc:
        report["avito"]["autoload_access"]=safe_error(exc)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print(json.dumps({"mode":"read_only","ozon_catalog":report["ozon"].get("total_catalog"),
                      "ozon_samples":report["ozon"].get("candidate_sample_count"),
                      "avito_ads":report["avito"].get("total_returned"),
                      "autoload_access":report["avito"].get("autoload_access"),
                      "report":str(OUTPUT.relative_to(ROOT))},ensure_ascii=False),flush=True)

if __name__=="__main__":
    run()
