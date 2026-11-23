"""LEOPARTS: restore one explicitly owner-approved price, 0am325025 from 12950 to 13850. Guarded, journaled.
Scope: Ozon seller prices and two canonical spreadsheets, selected SKU costs only.
Never mutate unrelated SKU, stock, WB/Avito/Tilda prices, formula, or promotions.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, os, subprocess, time, hashlib, sys
import openpyxl
import ozon_client as oz

ROOT=Path(__file__).resolve().parents[1]
MASTER=ROOT/"data/master_control.xlsx"
CATALOG=ROOT/"data/ozon_catalog.xlsx"
JOURNAL=ROOT/"data/ozon_restore_0am325025_20261123.json"
# SKU -> (approved old live seller price, approved new live seller price)
PLAN={
 "0am325025":(12950,13850),
}
# No purchase cost changes: only the approved Ozon price.
COSTS={}
assert len(PLAN)==1 and set(COSTS).issubset(PLAN)
HASH=hashlib.sha256(json.dumps({"prices":PLAN,"costs":COSTS},sort_keys=True).encode()).hexdigest()
def stamp(): return datetime.now(timezone.utc).isoformat()
def require(cond,msg):
    if not cond:raise RuntimeError("STOP: "+msg)
def num(v):
    if v is None:return None
    try:return round(float(str(v).replace(" ","").replace(",",".")),4)
    except Exception:return None
def eq(a,b):return a is not None and b is not None and abs(float(a)-float(b))<.01
def write(j):
    j["updated_utc"]=stamp()
    JOURNAL.write_text(json.dumps(j,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def checkpoint(*paths):
    relative=[str(p.relative_to(ROOT)) for p in paths]
    subprocess.run(["bash",".github/scripts/save_data.sh",*relative],cwd=ROOT,check=True,timeout=150)
def build_sheet_edits(path,sheet,price_col,cost_col=None):
    wb=openpyxl.load_workbook(path)
    require(sheet in wb.sheetnames,"Missing sheet "+sheet)
    ws=wb[sheet]
    cols={}
    for i in range(2,ws.max_row+1):
        val=ws.cell(i,1).value
        if isinstance(val,str) and val.strip() in PLAN:
            s=val.strip()
            require(s not in cols,"Duplicate SKU "+s+" in "+str(path))
            cols[s]=i
    require(set(cols)==set(PLAN),"Missing expected exact SKU in "+str(path)+": "+str(sorted(set(PLAN)-set(cols))))
    changes=[]
    for sku,(expected,target) in PLAN.items():
        row=cols[sku]
        current=num(ws.cell(row,price_col).value)
        require(eq(current,expected) or eq(current,target),
                f"Source price changed unexpectedly for {sku} {path}: {current}")
        if not eq(current,target):
            changes.append({"sku":sku,"column":price_col,"before":current,"after":target})
            ws.cell(row,price_col).value=target
        if cost_col and sku in COSTS:
            old,new=COSTS[sku]
            actual=num(ws.cell(row,cost_col).value)
            require(eq(actual,old) or eq(actual,new),
                    f"Master purchase cost changed unexpectedly for {sku}: {actual}")
            if not eq(actual,new):
                ws.cell(row,cost_col).value=new
                changes.append({"sku":sku,"column":cost_col,"before":actual,"after":new,"field":"purchase_cost"})
    return wb,changes
def validate_workbooks():
    # Column locations are defined by the existing LEOPARTS catalog contracts.
    m=openpyxl.load_workbook(MASTER,read_only=True,data_only=True)["Товары"]
    header=[str(c.value or "").strip() for c in m[1]]
    # Historical master price column is H and the owner cost column is located
    # by exact header name. The first columns have localized labels in this file.
    require(len(header)>=8 and "Закупка, ₽" in header,
            "Master cost/price columns missing: "+repr(header[:16]))
    cost_col=header.index("Закупка, ₽")+1
    require(cost_col not in (1,8),"Master cost cannot overwrite exact SKU or price")
    m.parent.close()
    v=openpyxl.load_workbook(CATALOG,read_only=True,data_only=True)["Ozon"]
    require(v.max_column>=4 and v.max_row>7,
            "Ozon price workbook schema unexpectedly short")
    v.parent.close()
    return cost_col
def live():
    prices=oz.get_prices_and_stocks(list(PLAN))
    details=oz.get_product_info_list(list(PLAN))
    result={}
    for sku in PLAN:
        row=[x for x in prices if str(x.get("offer_id") or "")==sku]
        detail=[x for x in details if str(x.get("offer_id") or "")==sku]
        require(len(row)==1 and len(detail)==1,"Missing or duplicate exact Ozon SKU "+sku)
        require(not detail[0].get("_error"),"Ozon SKU readback error "+sku)
        price=row[0].get("price") or {}
        status=(detail[0].get("statuses") or {})
        require(status.get("is_created") is True and status.get("moderate_status")=="approved",
                "Ozon SKU not approved: "+sku)
        found=num(price.get("price"))
        require(eq(found,PLAN[sku][0]) or eq(found,PLAN[sku][1]),
                f"Unexpected live Ozon price {sku}: {found}")
        old_min=num(price.get("min_price"))
        crossed=num(price.get("old_price"))
        result[sku]={"price":found,"min_price":old_min,"old_price":crossed,
                     "status":status.get("status_name")}
    return result
def run():
    require(os.environ.get("GITHUB_ACTIONS")=="true","Use only GitHub Actions journaled runner")
    require(os.environ.get("OZON_CLIENT_ID") and os.environ.get("OZON_API_KEY"),
            "Missing Ozon secrets")
    j=json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {
      "schema":1,"batch_id":"leoparts-restore-0am325025-13850-20261123",
      "approved":True,"fingerprint":HASH,"prices":{s:{"from":p[0],"to":p[1]} for s,p in PLAN.items()},
      "purchase_cost_updates":{s:{"from":x[0],"to":x[1]} for s,x in COSTS.items()},
      "state":"new","external_writes":0,"created_utc":stamp(),
    }
    require(j.get("fingerprint")==HASH,"Journal plan changed. Not allowed")
    if j.get("state") in ("intent_persisted","api_sent","unknown","verified"):
        # Recovery never blindly resubmits, even when an Ozon POST succeeded without ACK.
        prices=live()
        j["final_prices"]=prices
        j["state"]="verified" if all(eq(prices[s]["price"],PLAN[s][1]) for s in PLAN) else "needs_review"
        write(j);checkpoint(JOURNAL)
        print("OZON_RESTORE_025_RECONCILE",j["state"],flush=True)
        return
    require(j.get("state") in ("new","staged"),"Unknown journal state")
    current=live()
    j["before_live"]=current
    cost_col=validate_workbooks()
    wb1,updates1=build_sheet_edits(MASTER,"Товары",8,cost_col)
    wb2,updates2=build_sheet_edits(CATALOG,"Ozon",4)
    j["source_changes"]={"master":updates1,"ozon_catalog":updates2}
    if j["state"]=="new":
        # Safely save BOTH ledgers before any external Ozon request.
        wb1.save(MASTER);wb2.save(CATALOG)
        wb1.close();wb2.close()
        j["state"]="staged"
        write(j);checkpoint(MASTER,CATALOG,JOURNAL)
        print("OZON_RESTORE_025_LEDGER_STAGED",len(updates1),len(updates2),flush=True)
    else:
        wb1.close();wb2.close()
    # Verify saved workbook bytes/target prices on the runner before the write.
    m=openpyxl.load_workbook(MASTER,read_only=True,data_only=True)["Товары"]
    c=openpyxl.load_workbook(CATALOG,read_only=True,data_only=True)["Ozon"]
    for sheet,price_col in ((m,8),(c,4)):
        rows={str(row[0]):row for row in sheet.iter_rows(values_only=True) if row and row[0]}
        for sku in PLAN:
            require(sku in rows and eq(rows[sku][price_col-1],PLAN[sku][1]),
                    "Cannot verify durable staged price "+sku)
    m.parent.close();c.parent.close()
    # Build exact immutable single-attempt operation, keeping old_price unchanged.
    payload=[]
    for sku,(_,target) in PLAN.items():
        if eq(current[sku]["price"],target):continue
        price={"offer_id":sku,"price":str(target),"currency_code":"RUB"}
        crossed=current[sku]["old_price"]
        if crossed and crossed>target:price["old_price"]=str(int(crossed))
        minimum=current[sku]["min_price"]
        if minimum and minimum>target:price["min_price"]=str(target)
        payload.append(price)
    j["request_items"]=payload
    if not payload:
        j["final_prices"]=current;j["state"]="verified";write(j);checkpoint(JOURNAL)
        print("OZON_RESTORE_025_ALREADY_CORRECT",flush=True)
        return
    j["state"]="intent_persisted"
    write(j);checkpoint(JOURNAL)
    # IMPORTANT exactly ONE physical HTTP POST; further retries require read-only reconciliation.
    print("OZON_RESTORE_025_POST_BEGIN",len(payload),flush=True)
    try:
        reply=oz._post("/v1/product/import/prices",{"prices":payload},retries=1)
        rows=reply.get("result") if isinstance(reply,dict) else None
        j["ack"]=[{"offer_id":r.get("offer_id"),"updated":r.get("updated"),
                   "errors":r.get("errors")} for r in rows] if isinstance(rows,list) else []
        j["state"]="api_sent"
        j["external_writes"]=1
        write(j);checkpoint(JOURNAL)
    except Exception as ex:
        j["state"]="unknown";j["last_error"]=type(ex).__name__+": "+str(ex)[:260]
        write(j);checkpoint(JOURNAL)
        print("OZON_RESTORE_025_UNKNOWN_HTTP_OUTCOME",j["last_error"],flush=True)
        return
    # Read-only verification. Never re-send the price POST if processing is delayed.
    for wait in (3,8,15):
        time.sleep(wait)
        try:
            after=live()
            j["final_prices"]=after
            if all(eq(after[s]["price"],PLAN[s][1]) for s in PLAN):
                j["state"]="verified";break
        except Exception as ex:
            j["readback_error"]=type(ex).__name__+": "+str(ex)[:200]
    else:j["state"]="awaiting_readback"
    write(j);checkpoint(JOURNAL)
    print("OZON_RESTORE_025_FINAL",j["state"],j.get("final_prices",{}),flush=True)
if __name__=="__main__":run()
