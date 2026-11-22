"""Read-only inspect exact automotive fasteners category in Ozon for 6x65 screw.
Never use copied transmission or oil filter categories.
"""
import sys,json,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client
OUT=ROOT/"data/repair_vint_ea888_ozon_category_20261123.json"
CAT=74606916
def reduce_node(node):
    r={k:v for k,v in node.items() if k not in ("children","types","attributes","description")}
    for c in ("children","types"):
        if c in node and isinstance(node[c],list):
            r[c+"_count"]=len(node[c])
            r[c+"_samples"]=[reduce_node(x) for x in node[c][:40] if isinstance(x,dict)]
    return r
def scan():
    try: tree=ozon_client.get_category_tree("RU")
    except ozon_client.OzonApiError:
        tree=ozon_client._post("/v1/description-category/tree",{"language":"RU"}).get("result",[])
    results=[]
    pending=[(tree,[])]
    while pending:
        x,path=pending.pop()
        if isinstance(x,list):
            pending.extend((y,path) for y in x);continue
        if not isinstance(x,dict):continue
        name=str(x.get("category_name") or x.get("name") or x.get("type_name") or "")
        current=path+[name]
        if x.get("description_category_id")==CAT:
            results.append({"path":current,"raw":reduce_node(x)})
        if x.get("type_id") and any(term in "/".join(current).lower() for term in ("креп","винт","болт")):
            results.append({"path":current,"type_leaf":reduce_node(x)})
        pending.append((x.get("children") or x.get("types") or [],current))
    out={"category":CAT,"matches":results[:160]}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False,default=str)+"\n")
    print(json.dumps({"category":CAT,"result_count":len(results),"matches":results[:7]},ensure_ascii=False,default=str),flush=True)
if __name__=="__main__":scan()
