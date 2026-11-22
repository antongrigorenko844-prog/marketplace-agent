"""Read-only current Avito pricing rule audit from canonical repo data.

No marketplace writes, no prices changed, no uploads. Only shared internal
master_control and channel-specific price override sheets are read.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import os
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import site_pricing as sp

OUT=ROOT/"data/avito_pricing_live_audit_20261123.json"
MASTER=ROOT/"data/master_control.xlsx"
OVERRIDES=sp.OVERRIDES_PATH
def money(x):
    if x is None:return None
    return round(float(x),2)
def main():
    d={"at_utc":datetime.now(timezone.utc).isoformat(),"mode":"READ_ONLY","external_writes":0,
       "master_path":str(MASTER.relative_to(ROOT)),"overrides_path":"data/avito_site_price_overrides.xlsx"}
    try:
        share,fixed=sp._read_meta(OVERRIDES)
        settings=sp.get_profit_settings(OVERRIDES)
        premium_limit,premium_share=sp.get_premium(OVERRIDES)
        d.update(settings={
            "mode":settings["mode"],"tax_rate":settings["tax"],"avito_fee_assumed":settings["avito_fee"],
            "site_fee_assumed":settings["site_fee"],"base_share":share,
            "premium_from_rub":premium_limit,"premium_share":premium_share,
            "minimum_markup_to_purchase":sp.get_min_markup(OVERRIDES),
            "freeze_expensive_from_ozon_rub":sp.get_freeze_from(OVERRIDES),
            "assumed_advertising_share":sp.AD_SHARE,
            "minimum_profit_without_cost":sp.MIN_PROFIT,
            "expensive_price_freeze_applies_only_in_profit_mode":True,
            "nice_price_step_description":"Different piecewise levels 950/1850/..850, above 30000 ..500; see site_pricing.nice_price"
        },manually_fixed_count=len(fixed),manually_fixed_sample=sorted(fixed)[:30])
        old=sp.load_price_overrides(OVERRIDES,"avito")
        site=sp.load_price_overrides(OVERRIDES,"site")
        d["counts"]={"avito_override_rows":len(old),"site_override_rows":len(site),
                     "manually_fixed_rows":len(fixed)}
        plans=sp.plan_equal_profit(str(MASTER),OVERRIDES,settings)
        d["products"]=[{"sku":r["art"],"name":str(r["name"])[:125],"ozon_price":money(r["ozon_price"]),
                         "cost":money(r["cost"]),"avito_current":money(r["avito_old"]),
                         "site_current":money(r["site_old"]),
                         "avito_profit_model_price":money(r["avito_new"]),
                         "fixed":bool(r["fixed"]),"would_be_price_source":str(r["src"])[:180]}
                        for r in plans]
        d["models_available_count"]=sum(x["cost"] is not None for x in d["products"])
        # This is a preview only, NEVER write to pricing workbook.
        changed=sp.recalc_from_master(str(MASTER),OVERRIDES,dry_run=True)
        d["auto_reprice_preview"]=[{"sku":x[0],"old":money(x[1]),"planned":money(x[2])} for x in changed[:200]]
        d["auto_reprice_preview_count"]=len(changed)
        d["status"]="complete"
    except Exception as e:
        d["status"]="error";d["error_type"]=type(e).__name__;d["error"]=str(e)[:340]
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"status":d.get("status"),"settings":d.get("settings"),"counts":d.get("counts"),
                      "auto_reprice_preview_count":d.get("auto_reprice_preview_count"),"error":d.get("error")},ensure_ascii=False))
    return 0 if d["status"]=="complete" else 1
if __name__=="__main__":sys.exit(main())
