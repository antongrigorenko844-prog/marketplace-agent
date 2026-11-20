"""Fill the single WB kit composition verified by its exact component bill."""
import copy, hashlib, json, logging, re, sys
from collections import Counter
from pathlib import Path
from openpyxl import load_workbook
sys.path.insert(0,str(Path.cwd()))
import wb_client as wb
from content_sync import apply_wb
from content_transport import guard
logging.disable(logging.CRITICAL)
SKU, NM, SUBJECT, FIELD = "remkomplekt-shtokov-orig", 1949668977, 5823, 378533
VALUES = ["Пыльник штока — 2 шт.", "Сальник штока — 2 шт.", "Крышка штока — 2 шт."]
book=load_workbook("data/kits.xlsx",read_only=True,data_only=True)
rows=[(r[2],r[3]) for r in book["Комплекты"].values if r[0]==SKU]
book.close()
if Counter(rows)!=Counter([("122390AKAF",2),("0сц",2),("0am325025q",2)]):
    raise ValueError("Kit bill changed")
defs=wb.get_subject_characteristics(SUBJECT)
if len([r for r in defs if r.get("name")=="Комплектация" and r.get("charcID")==FIELD and r.get("charcType")==1])!=1:
    raise ValueError("WB field definition changed")
def exact():
    cards=wb.list_cards()
    found=[c for c in cards if c.get("vendorCode")==SKU or c.get("nmID")==NM]
    if len(found)!=1 or (found[0]["vendorCode"],found[0]["nmID"],found[0]["subjectID"])!=(SKU,NM,SUBJECT):
        raise ValueError("WB identity changed")
    return found[0]
def protected(c):
    other={k:v for k,v in c.items() if k not in {"characteristics","updatedAt"}}
    other["characteristics"]=sorted([v for v in c.get("characteristics",[]) if v["id"]!=FIELD],key=lambda x:x["id"])
    return hashlib.sha256(json.dumps(other,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def get_comp(c):
    val=[p["value"] for p in c.get("characteristics",[]) if p.get("id")==FIELD]
    if len(val)>1:raise ValueError("Duplicate composition attributes")
    return val[0] if val else []
previous=exact()
before_hash=protected(previous)
if not all(token in previous["description"].lower() for token in ("2 пыльника","2 сальника","2 крышки")):
    raise ValueError("Live description changed")
fresh=exact()
if fresh!=previous:raise ValueError("Concurrent WB change")
before=get_comp(fresh)
if before not in ([],VALUES):raise ValueError("Existing nonempty composition")
writes=[]
if before==VALUES:
    after=fresh
else:
    projected=copy.deepcopy(fresh)
    if not any(a["id"]==FIELD for a in projected["characteristics"]):
        projected["characteristics"].append({"id":FIELD,"name":"Комплектация","value":[]})
    rule={"ids":{"wb":NM},"wb_characteristics":[{"id":FIELD,"value":VALUES}]}
    with guard(writes,allow_write=True):
        apply_wb(SKU,rule,{"cards":[projected]})
    after=exact()
if protected(after)!=before_hash or get_comp(after)!=VALUES or len(writes)!=(0 if before else 1):
    raise ValueError("WB content or scope readback did not match")
print("VERIFIED",SKU,NM,"before",before,"after",get_comp(after),"other_fields_same",True,"writes",len(writes))
