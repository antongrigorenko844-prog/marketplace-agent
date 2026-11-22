"""Scope: only three previously created WB 6T family products.
Read back actual sale prices, submit owner-approved prices only where necessary.
"""
import json,os,sys,time,re
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
OUT=ROOT/"data/6t_family_20261123_wb_price_verify_result.json"
EXPECTED={"6t30-solenoids-gen2":(2338952761,19880),
          "6t30-tcm-gen1":(2338952880,58850),
          "6t30-tcm-gen2":(2338953074,58850)}
def emit(name,x):
    try:d=json.loads(OUT.read_text())
    except (OSError,ValueError):d={}
    d[name]=x;d["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"phase":name,"result":x},ensure_ascii=False),flush=True)
def safeerr(e):
    s=str(e)
    m=re.search(r"вернул ([45]\d\d):\s*(.{0,300})",s)
    return {"type":type(e).__name__,"http_status":int(m.group(1)) if m else None,
            "details":m.group(2) if m else None}
def rows():
    allrows=wb_client.get_prices(limit=1000)
    matches={}
    for r in allrows:
        sku=r.get("vendorCode")
        if sku in EXPECTED:
            if sku in matches:raise ValueError("Duplicate price row for "+sku)
            if int(r.get("nmID") or 0)!=EXPECTED[sku][0]:raise ValueError("Incorrect nmID for "+sku)
            matches[sku]=r
    return matches
def main():
    req=json.loads((ROOT/"data/6t_family_20261123_wb_price_verify_request.json").read_text())
    if req.get("skus")!=list(EXPECTED) or req.get("allow_other_price_changes") is not False:
        raise ValueError("Unapproved price scope")
    manifest=json.loads((ROOT/"data/supplier_batch_20261123_card_drafts.json").read_text())
    by={x["sku"]:x for x in manifest["confirmed_missing_product_drafts"]}
    for sku,(_,price) in EXPECTED.items():
        if by[sku]["prices_rub"]["wildberries"]!=price:raise ValueError("Owner prices changed")
    cards=wb_client.list_cards(limit=100)
    names={x["vendorCode"]:x for x in cards if x.get("vendorCode") in EXPECTED}
    if set(names)!=set(EXPECTED) or any(int(names[s]["nmID"])!=EXPECTED[s][0] for s in EXPECTED):
        emit("preflight",{"status":"missing_or_duplicate_or_wrong_card","found":list(names)})
        return
    emit("preflight",{"status":"three_cards_confirmed","ids":{k:v[0] for k,v in EXPECTED.items()}})
    try: current=rows()
    except Exception as e:
        emit("prices_read",{"error":safeerr(e)})
        return
    status={};submitted={}
    for sku,(nmid,target) in EXPECTED.items():
        row=current.get(sku)
        if row and len(row.get("sizes") or [])==1:
            existing=float(row["sizes"][0].get("discountedPrice") or 0)
            if abs(existing-target)<=1:
                status[sku]={"status":"already_correct","actual":existing}
                continue
            if existing not in (0,target):
                status[sku]={"status":"unexpected_price","actual":existing}
                continue
        try:
            ans=wb_client.update_prices([{"nmID":nmid,"price":target,"discount":0}])
            task=(ans.get("data") or {}).get("id")
            submitted[sku]=task
            status[sku]={"status":"request_accepted","upload_id":task}
        except Exception as e:
            status[sku]={"status":"price_api_rejected","error":safeerr(e)}
        emit("upload",status)
    time.sleep(10)
    try:
        new=rows()
        for sku,(_,target) in EXPECTED.items():
            x=new.get(sku);sizes=(x or {}).get("sizes") or []
            actual=float(sizes[0].get("discountedPrice")) if len(sizes)==1 else None
            status.setdefault(sku,{})
            status[sku].update({"actual":actual,"verified":actual is not None and abs(actual-target)<=1})
    except Exception as e:status["readback_error"]=safeerr(e)
    emit("final",status)
if __name__=="__main__":main()
