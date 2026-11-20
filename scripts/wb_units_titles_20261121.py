"""Apply verified exact-SKU retail pack counts and titles, with per-card readback."""
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
import ozon_client as oz
import wb_client as wb
from content_transport import guard

PLAN = json.loads(Path("scripts/wb_units_titles_20261121.json").read_text())
QTY = 90702
COMP = 378533
receipt = {"at": datetime.now(timezone.utc).isoformat(), "changed": {}, "already": [], "skipped": {}, "write_batches": 0}
assert len(PLAN)==46 and not set(PLAN) & {"0cw0am","0cw0amdsgdq200","0cw325025h","TESTWB001"}
assert all("0bh" not in sku.lower() for sku in PLAN)
def indexed(cards, field):
    result = {r[field]:r for r in cards}
    assert len(result)==len(cards), "Ambiguous identifiers"
    return result

def attr(c, fid):
    vals = [v["value"] for v in c.get("characteristics",[]) if v["id"]==fid]
    assert len(vals)<=1, "Repeated characteristic"
    return vals[0] if vals else None
def normalized(c, exclude=()):
    out={k:v for k,v in c.items() if k not in {"updatedAt","characteristics"}|set(exclude)}
    out["characteristics"]=sorted([x for x in c.get("characteristics",[]) if x["id"] not in exclude], key=lambda x:x["id"])
    return out
def excluded(spec):
    return (["title"] if "title" in spec else []) + ([QTY] if "qty" in spec else [])
def goal(c,s):
    return ("title" not in s or c["title"]==s["title"]) and ("qty" not in s or attr(c,QTY)==s["qty"])
def allowed(c,s,ozon,defs):
    if not c or (c["nmID"],c["subjectID"])!=(s["nm"],s["subject"]):return "identity"
    if attr(c,COMP)!=s["composition"]:return "composition changed"
    if "title" in s and c["title"] not in (s["old"],s["title"]):return "different existing title"
    if "qty" not in s:return None
    if c['subjectID'] not in defs:return "field unavailable in WB subject"
    now=attr(c,QTY)
    if now is not None and now!=s['qty']:return "existing pack count"
    o=ozon.get(c["vendorCode"])
    if not o or o["id"]!=s["ozid"]:return "source identity"
    ov=[v["value"] for a in o["attributes"] if a["id"]==8513 for v in a["values"]]
    if ov!=[str(s["qty"])]:return "Ozon pack count changed"
    return None
def payload(c,s):
    out={k:copy.deepcopy(c[k]) for k in ["nmID","vendorCode","brand","title","description","dimensions","characteristics","sizes"]}
    out["kizMarked"]=c.get("kizMarked",False)
    out["sizes"]=[{k:v for k,v in size.items() if k in {"chrtID","techSize","wbSize","skus"}} for size in out["sizes"]]
    if "title" in s:out["title"]=s["title"]
    if "qty" in s and attr(c,QTY)!=s["qty"]:
        assert attr(c,QTY) is None
        out["characteristics"]=[dict(a) for a in out["characteristics"] if a["id"]!=QTY]+[{"id":QTY,"value":s["qty"]}]
    return out
def save():
    print("RECEIPT",json.dumps(receipt,ensure_ascii=False),flush=True)
    Path("wb_verified_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
def run():
    ozon=indexed(oz.get_product_names([k for k,s in PLAN.items() if "qty" in s]),"offer_id")
    before=indexed(wb.list_cards(),"vendorCode")
    assert len(before)>=130 and len(set(c["nmID"] for c in before.values()))==len(before)
    defs=set()
    for sid in sorted({s["subject"] for s in PLAN.values() if "qty" in s}):
        aa=[a for a in wb.get_subject_characteristics(sid) if a["charcID"]==QTY]
        if len(aa)==1 and (aa[0]["name"],aa[0]["charcType"],aa[0].get("unitName"))==("Количество предметов в упаковке (шт.)",4,"шт."):defs.add(sid)
    order=[]
    for k,s in PLAN.items():
        c=before.get(k); reason=allowed(c,s,ozon,defs)
        if reason:receipt["skipped"][k]=reason
        elif goal(c,s):receipt["already"].append(k)
        else:order.append(k)
    canaries=[k for k in ("klipsy-shtokov-2sht","0am325025") if k in order]
    if not canaries and order:canaries=order[:1]
    rest=[k for k in order if k not in canaries]
    batches=([canaries] if canaries else [])+[rest[i:i+15] for i in range(0,len(rest),15)]
    for batch in batches:
        fresh=indexed(wb.list_cards(),"vendorCode")
        targets=[]
        for k in batch:
            if normalized(before[k])!=normalized(fresh[k]) or before[k]["updatedAt"]!=fresh[k]["updatedAt"]:
                receipt["skipped"][k]="changed since preflight";continue
            targets.append(k)
        if not targets:continue
        request=[payload(fresh[k],PLAN[k]) for k in targets]
        result=wb.update_cards(request)
        if not result or result.get("error") is not False:raise ValueError("WB rejected batch "+repr(result)[:220])
        receipt["write_batches"]+=1
        print("ACCEPTED",len(targets),flush=True)
        pending=set(targets)
        for delay in [4,6,10,15,20,25,30,30,30,30,30,30]:
            time.sleep(delay)
            after=indexed(wb.list_cards(),"vendorCode")
            for k in list(pending):
                c=after.get(k);s=PLAN[k]
                if not c or not goal(c,s):continue
                if normalized(c,excluded(s))!=normalized(fresh[k],excluded(s)):
                    raise ValueError("Other WB fields changed on "+k)
                record={"nm":s["nm"]}
                if "qty" in s:record["qty"]=attr(c,QTY)
                if "title" in s:record["title"]=c["title"]
                receipt["changed"][k]=record;pending.remove(k)
            if not pending:break
        save()
        if pending:raise ValueError("Readback pending: "+repr(sorted(pending)))
if __name__=='__main__':
    try:
        with guard([],allow_write=True):run()
    finally:save()
