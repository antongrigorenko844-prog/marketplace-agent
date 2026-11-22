"""Append the exact three already-created WB 6T30 cards to master_control.xlsx.

Protect all original rows, embedded images, formulas and other SKUs. Do not
submit Ozon/WB cards from master, and do not pretend to attach product photos.
"""
import json,zipfile,hashlib,copy
from pathlib import Path
from datetime import datetime,timezone
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"data/master_control.xlsx"
DRAFT=ROOT/"data/supplier_batch_20261123_card_drafts.json"
EVIDENCE=ROOT/"data/6t_family_20261123_publish_wb_result.json"
OUT=ROOT/"data/leoparts_6t30_master_append_20261123_result.json"
TARGET=["6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2"]
EXPECTED_PRICE=[19880,58850,58850]; EXPECTED_QTY=[20,3,3]
def media_hash(path):
    with zipfile.ZipFile(path) as z:
        return {n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith("xl/media/")}
def main():
    wb=load_workbook(PATH)
    sheet=wb["Товары"]
    header=[sheet.cell(1,c).value for c in range(1,29)]
    if header[0]!="Артикул (offer_id)" or header[9]!="Остаток, шт." or header[10]!="Цена WB, ₽ (финальная, покупателю)":
        raise ValueError("Master control sheet schema changed")
    count=sheet.max_row
    ids=[str(sheet.cell(r,1).value or "").strip() for r in range(2,count+1)]
    if len(ids)!=len(set(x.casefold() for x in ids if x)):raise ValueError("Master already has duplicate SKUs")
    drafts=json.loads(DRAFT.read_text())["confirmed_missing_product_drafts"]
    products={x["sku"]:x for x in drafts}
    evidence=json.loads(EVIDENCE.read_text())
    for i,sku in enumerate(TARGET):
        creation=(evidence.get("creation") or {}).get(sku) or {}
        price=(evidence.get("price_confirmation") or {}).get(sku) or {}
        if creation.get("status")!="card_created" or not creation.get("nmID"):
            raise ValueError("WB creation not independently confirmed: "+sku)
        if not price.get("status")=="verified" or float(price.get("observed_price_rub") or 0)!=EXPECTED_PRICE[i]:
            raise ValueError("WB price verification missing: "+sku)
        if products[sku]["prices_rub"]["wildberries"]!=EXPECTED_PRICE[i] or products[sku]["stock_LEOPARTS"]!=EXPECTED_QTY[i]:
            raise ValueError("approved commercial input mismatch")
    old_values=[[sheet.cell(r,c).value for c in range(1,29)] for r in range(1,count+1)]
    original_media=media_hash(PATH)
    if any(s in ids for s in TARGET):
        present=[s for s in TARGET if s in ids]
        if len(present)!=3:raise ValueError("Partial prior insertion; explicit reconciliation needed")
        created=[]
    else:
        created=[]
        for i,sku in enumerate(TARGET):
            p=products[sku];r=sheet.max_row+1;style_row=sheet[155]
            for col in range(1,29):
                dst=sheet.cell(r,col);src=sheet.cell(155,col)
                if src.has_style:dst._style=copy.copy(src._style)
                dst.alignment=copy.copy(src.alignment)
                dst.protection=copy.copy(src.protection)
            title=p["name_ru"]
            wb_title=(evidence["creation"][sku]["title"])
            if len(wb_title)>60:raise ValueError("WB title too long")
            dim=(0.8,12,9,8) if i==0 else (3.5,30,25,12)
            data={
              1:sku,2:None,3:"Действующий",5:0,6:title,7:wb_title,
              8:EXPECTED_PRICE[i],10:EXPECTED_QTY[i],11:EXPECTED_PRICE[i],
              12:p["description"],14:("23.11.2026: WB nmID "+str(evidence["creation"][sku]["nmID"])+
                "; фото утверждено владельцем в библиотеке ChatGPT, но ещё не загружено на площадку. Остальные каналы — ожидание."),
              15:None,16:None,23:dim[0],24:dim[1],25:dim[2],26:dim[3]
            }
            for col,val in data.items():sheet.cell(r,col).value=val
            created.append({"sku":sku,"row":r,"nmID":evidence["creation"][sku]["nmID"]})
    for r,row in enumerate(old_values,1):
        if row != [sheet.cell(r,c).value for c in range(1,29)]:
            raise ValueError("Existing master cell changed in row "+str(r))
    if created:
        temp=PATH.with_name("master_control.next.xlsx")
        wb.save(temp)
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:raise RuntimeError("Corrupt new master")
        if media_hash(temp)!=original_media:raise ValueError("Workbook embedded media changed")
        probe=load_workbook(temp,read_only=True,data_only=True)
        ids2=[str(probe["Товары"].cell(r,1).value or "").strip() for r in range(2,probe["Товары"].max_row+1)]
        probe.close()
        if any(ids2.count(s)!=1 for s in TARGET):raise ValueError("New master SKU reconciliation failed")
        temp.replace(PATH)
    wb.close()
    result={"status":"three_appended" if created else "already_all_present","original_rows":count,
            "new_rows":created,"photo_media_bytes_preserved":True,
            "existing_rows_unchanged":True,"checked_utc":datetime.now(timezone.utc).isoformat()}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
