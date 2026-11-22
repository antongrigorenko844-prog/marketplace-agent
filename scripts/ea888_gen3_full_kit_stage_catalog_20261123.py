"""Stage only one new LEOPARTS EA888 Gen3 full oil filter kit in canonical
master, Ozon export, WB export and fixed site/Avito price table.
The old EA888gen3 kit is never modified. Do not infer filter acquisition cost.
"""
import json,sys,copy
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
SKU="EA888gen3-filter-kit"
ORIGINAL="EA888gen3"
DATA=ROOT/"data"
OUT=DATA/"ea888_gen3_full_kit_catalog_stage_20261123_result.json"
def load():return json.loads((DATA/"ea888_gen3_filter_kit_approved_20261123.json").read_text())
def idx(ws,keycol,sku):
    hits=[i for i in range(2,ws.max_row+1) if ws.cell(i,keycol).value==sku]
    if len(hits)>1:raise ValueError(f"duplicate {sku} in {ws.title}")
    return hits[0] if hits else None
def create_from_sample(ws,keycol,source):
    sample=idx(ws,keycol,source)
    if sample is None:raise ValueError("Source kit "+source+" missing in "+ws.title)
    row=ws.max_row+1
    for col in range(1,ws.max_column+1):
        old,new=ws.cell(sample,col),ws.cell(row,col)
        new.value=old.value
        if old.has_style:new._style=copy.copy(old._style)
        if old.number_format:new.number_format=old.number_format
    return row
def value(ws,row,col,desired,changes):
    old=ws.cell(row,col).value
    if old!=desired:
        ws.cell(row,col).value=desired
        changes.append({"sheet":ws.title,"cell":f"{get_column_letter(col)}{row}","before":old,"after":desired})
def stage():
    p=load()
    if p.get("sku")!=SKU or p.get("source_sku")!=ORIGINAL or p["price_rub"]["ozon"]!=7850 or p["saleable_stock"]!=40:return 1
    img=json.loads((DATA/"ea888_gen3_full_kit_photos_20261123_result.json").read_text())["urls"]
    files=[SKU+"_1.jpg",SKU+"_2.jpg",SKU+"_3.jpg"]
    if not all(f in img for f in files):raise ValueError("New gallery incomplete")
    image_urls="|".join(img[x] for x in files)
    plans=[]
    for path,sheet,source,col in [
      ("data/master_control.xlsx","Товары",ORIGINAL,1),
      ("data/ozon_catalog.xlsx","Ozon",ORIGINAL,1),
    ]:
        wb=load_workbook(ROOT/path);ws=wb[sheet]
        row=idx(ws,col,SKU);new=not row
        if new:row=create_from_sample(ws,col,source)
        changes=[]
        value(ws,row,1,SKU,changes)
        if sheet=="Товары":
            data={
              2:"Создание" if new else ws.cell(row,2).value,3:"Действующий",4:None,5:3,
              6:p["product_name"],7:p["title_by_channel"]["wildberries"],
              8:7850,9:None,10:40,11:7850,12:p["text"]["ozon"],
              13:"#EA888 #EA888Gen3 #масляный_фильтр #корпус_фильтра #обратный_клапан",
              14:"Комплект корпус + клапан + сменный фильтр + кольцо. 40 наборов подтверждено владельцем. Себестоимость фильтра уточняется.",
              15:"|".join(files),16:"8421230000",
              17:"да",18:"да",19:"да",20:"да",21:"да",
              22:None,23:0.8,24:18,25:12,26:10,27:None,28:None
            }
        else:
            data={
              2:p["title_by_channel"]["ozon"],3:p["text"]["ozon"],4:7850,5:None,
              6:image_urls,7:None,8:40
            }
            # Ozon catalog has source columns such as notes and customs tariff;
            # never retain source price, SKU, or physical item identifier.
        for c,v in data.items():value(ws,row,c,v,changes)
        plans.append((path,wb,new,changes))
    path="data/wb_catalog.xlsx"
    wb=load_workbook(ROOT/path);ws=wb["WB"]
    row=idx(ws,1,SKU);new=not row
    if new:
        row=ws.max_row+1
        source=idx(ws,1,"6t30")
        if source:
            for col in range(1,9):
                a,b=ws.cell(source,col),ws.cell(row,col)
                if a.has_style:b._style=copy.copy(a._style)
    changed=[]
    for c,v in {1:SKU,2:p["title_by_channel"]["wildberries"][:60],
                3:p["text"]["wildberries"],4:image_urls,5:None,6:7850,7:0,8:"Комплект корпуса EA888 Gen3 + клапан + фильтр и кольцо"}.items():
        value(ws,row,c,v,changed)
    plans.append((path,wb,new,changed))
    path="data/avito_site_price_overrides.xlsx"
    wb=load_workbook(ROOT/path);ws=wb["Цены Avito и сайт"]
    row=idx(ws,1,SKU);new=not row
    if new:row=ws.max_row+1
    changed=[]
    for c,v in {1:SKU,2:4350,3:"да",4:4350}.items():value(ws,row,c,v,changed)
    plans.append((path,wb,new,changed))
    # Price source preflight all files before any save.
    if any((not changes and new) for _,_,new,changes in plans):
        raise ValueError("Empty staged new SKU")
    report={"sku":SKU,"staged_at_utc":datetime.now(timezone.utc).isoformat(),"scope":"single newly created SKU only",
        "source_kept_untouched":ORIGINAL,"price_marketplaces":7850,"price_avito_site":4350,"stock":40,
        "files":{path:{"new_row":new,"changes":changes} for path,_,new,changes in plans},
        "photos":files,"catalog_photo_count":len(files)}
    for path,wb,_,changes in plans:
        if changes:wb.save(ROOT/path)
        wb.close()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({path:{"new":v["new_row"],"num_changes":len(v["changes"])} for path,v in report["files"].items()},ensure_ascii=False),flush=True)
    return 0
if __name__=="__main__":raise SystemExit(stage())
