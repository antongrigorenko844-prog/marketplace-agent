"""Read-only inspect required Ozon attributes for Metiz Avtomobilny
(auto screw) vs Bolt Avtomobilny, and valid customs code for EA888 M6 screw."""
import sys,json,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client
OUT=ROOT/"data/repair_vint_ea888_ozon_validate_type_20261123.json"
C=74606916
TYPES={"Метиз автомобильный":970858643,"Болт автомобильный":970840917}
def run():
 d={"at":datetime.now(timezone.utc).isoformat(),"category":C,"scope":"read_only"}
 for name,typ in TYPES.items():
    x={"type_id":typ}
    try:
        attrs=ozon_client.get_category_attributes(C,typ)
        x["attrs"]=[{"id":a.get("id"),"name":a.get("name"),"required":a.get("is_required"),
            "type":a.get("type"),"dictionary_id":a.get("dictionary_id"),"is_collection":a.get("is_collection")} for a in attrs]
        x["required"]=[a for a in x["attrs"] if a.get("required")]
        x["attribute_count"]=len(attrs)
        for attr_id,query in [(22232,"731815"),(22232,"7318"),(8229,"метиз")]:
            try:
                z=ozon_client.search_attribute_values(C,typ,attr_id,query,limit=15)
                x[f"dictionary_{attr_id}_{query}"]=z[:12]
            except Exception as exc:x[f"dictionary_{attr_id}_{query}"]={"error_type":type(exc).__name__}
    except Exception as exc:x["error_type"]=type(exc).__name__
    d[name]=x
 OUT.write_text(json.dumps(d,indent=2,ensure_ascii=False,default=str)+"\n")
 print(json.dumps({"types":{k:{"count":v.get("attribute_count"),"required":v.get("required"),"dictionary_731815":v.get("dictionary_22232_731815")} for k,v in d.items() if k in TYPES}},ensure_ascii=False),flush=True)
if __name__=="__main__":run()
