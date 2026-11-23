"""LEOPARTS reusable exact-SKU stock adjustment (safe inventory writer).

Single physical quantity source: ozon_catalog.xlsx, column 8. master_control.xlsx
column 10 is a read/edit view and must agree after approved manual adjustment.
No seller API calls, no Avito, no price changes, no automatic inventory sync.
Run under marketplace-agent GitHub concurrency lock.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
REQUEST=ROOT/"data/stock_quantity_adjust_request.json"
CATALOG=ROOT/"data/ozon_catalog.xlsx"
MASTER=ROOT/"data/master_control.xlsx"
REPORT=ROOT/"data/stock_quantity_adjust_result.json"
SKU_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,100}$")
TARGET_FILES=((CATALOG,8,"quantity_to_sell"),(MASTER,10,"quantity_master"))

def require(condition,message):
    if not condition:
        raise ValueError(message)

def read_request():
    data=json.loads(REQUEST.read_text(encoding="utf-8"))
    sku=data.get("sku")
    quantity=data.get("quantity")
    require(type(sku) is str and SKU_RE.fullmatch(sku),"Unsafe SKU")
    require(type(quantity) is int and 0<=quantity<=100000,"Stock must be a nonnegative integer")
    require(data.get("mode") in ("inspect","apply"),"Unknown mode")
    require(data.get("approved") is True,"Owner approval missing")
    return data

def sha(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def lookup(path:Path, col:int, sku:str):
    require(path.is_file(),f"Stock source not found: {path.name}")
    wb=load_workbook(path)
    ws=wb["Товары"] if path==MASTER and "Товары" in wb.sheetnames else wb.active
    all_rows=[i for i in range(2,ws.max_row+1) if str(ws.cell(i,1).value or "").strip()==sku]
    require(len(all_rows)<=1,f"Duplicate exact SKU in {path.name}: {sku}")
    alt=[str(ws.cell(i,1).value).strip() for i in range(2,ws.max_row+1)
         if ws.cell(i,1).value is not None and
         str(ws.cell(i,1).value).strip().casefold()==sku.casefold()]
    require(len(alt)<=1 or all(x==sku for x in alt),
            f"Ambiguous SKU case mapping in {path.name}: {alt}")
    row=all_rows[0] if all_rows else None
    cell=ws.cell(row,col) if row else None
    value=cell.value if cell else None
    require(value is None or (type(value) in (int,float) and int(value)==value and value>=0),
            f"Unsafe existing stock type/formula in {path.name}: {value!r}")
    report={"file":str(path.relative_to(ROOT)),"present":bool(row),"row":row,
            "before":int(value) if value is not None else None,
            "sha256_before":sha(path)}
    return wb,ws,row,report

def persist(report):
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    tmp=REPORT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(REPORT)
    print(json.dumps({"sku":report["sku"],"mode":report["mode"],"state":report["state"],
                      "targets":report["files"]},ensure_ascii=False),flush=True)

def main():
    req=read_request()
    sku=req["sku"]; quantity=req["quantity"]; mode=req["mode"]
    report={"sku":sku,"quantity":quantity,"mode":mode,
            "checked_utc":datetime.now(timezone.utc).isoformat(),
            "files":{},"state":"inspected","seller_api_writes":0,
            "pricing_writes":0,"avito_writes":0}
    loaded=[]
    try:
        for path,col,label in TARGET_FILES:
            wb,ws,row,r=lookup(path,col,sku)
            loaded.append((path,col,wb,ws,row))
            report["files"][label]=r
        both=all(row is not None for _,_,_,_,row in loaded)
        if not both:
            report["state"]="blocked_missing_source_row"
            persist(report)
            if mode=="apply":
                raise ValueError("Cannot apply: exact SKU must exist in BOTH warehouse and master. Reconcile catalog first.")
            return
        if mode=="inspect":
            persist(report)
            return
        # No other product's cells or price columns are changed.
        changed=[]
        for path,col,wb,ws,row in loaded:
            cell=ws.cell(row,col)
            cell.value=quantity
            report["files"]["quantity_to_sell" if path==CATALOG else "quantity_master"]["after"]=quantity
            changed.append((path,wb))
        # Write to temporary copies first; persistence happens together via git commit.
        tmp_files=[]
        for path,wb in changed:
            fd,tmp=tempfile.mkstemp(prefix="stock_adjust_",suffix=".xlsx",dir=path.parent)
            os.close(fd)
            wb.save(tmp)
            checked=load_workbook(tmp,read_only=True,data_only=True)
            sheet=checked["Товары"] if path==MASTER and "Товары" in checked.sheetnames else checked.active
            rows=[r for r in sheet.iter_rows(min_row=2,values_only=True) if r[0] and str(r[0]).strip()==sku]
            require(len(rows)==1 and rows[0][8-1 if path==CATALOG else 10-1]==quantity,
                    "Post-save verification failed: "+path.name)
            checked.close()
            tmp_files.append((path,tmp))
        for path,tmp in tmp_files:
            os.replace(tmp,path)
        for path,col,wb,ws,row in loaded:
            report["files"]["quantity_to_sell" if path==CATALOG else "quantity_master"]["sha256_after"]=sha(path)
        report["state"]="warehouse_quantity_updated"
        persist(report)
    except Exception as e:
        if report["state"]!="blocked_missing_source_row":
            report["state"]="blocked_error"
            report["error"]=str(e)[:350]
            persist(report)
        raise

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--request",type=Path,default=REQUEST)
    args=parser.parse_args()
    require(args.request.resolve()==REQUEST.resolve(),"Only approved stable request path permitted")
    main()
