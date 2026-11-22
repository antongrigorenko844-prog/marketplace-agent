"""Read-only live Wildberries card identity scan for smooth DQ500 housing."""
import json,re,time,sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client
from openpyxl import load_workbook

SKU="Dq500"
OUT=Path("data/dq500_wb_scan_20261123.json")
cards=wb_client.list_cards(limit=100)
prices=wb_client.get_prices(limit=1000)
wb=load_workbook("data/master_control.xlsx",read_only=True,data_only=True)
ws=wb["Товары"]
head=[c.value for c in ws[1]]
matching=[r for r in ws.iter_rows(min_row=2,values_only=True) if r[0]==SKU]
wb.close()
if len(matching)!=1:raise RuntimeError("Dq500 not uniquely in master")
r=matching[0]
print("WB cards received",len(cards),"WB price rows",len(prices),flush=True)
scoped=[]
for card in cards:
    vc=str(card.get("vendorCode") or "")
    title=str(card.get("title") or "")
    descr=str(card.get("description") or "")
    if (vc.casefold()==SKU.casefold() or
        re.search(r"(?i)dq.?500|0bh325|корпус фильтр",title+" "+vc) or
        ("0BH325" in descr and "корпус" in title.lower())):
        safe={"vendorCode":vc,"nmID":card.get("nmID"),"subjectID":card.get("subjectID"),"subjectName":card.get("subjectName"),"title":title,
        "description_excerpt":descr[:320],"brand":card.get("brand"),"photos_count":len(card.get("photos") or []),
        "dimensions":card.get("dimensions"),"sizes":[{"techSize":x.get("techSize"),"wbSize":x.get("wbSize"),"skus_count":len(x.get("skus") or []),"price":x.get("price")} for x in (card.get("sizes") or [])],
        "characteristics":[{"id":x.get("id"),"name":x.get("name"),"value":x.get("value")} for x in (card.get("characteristics") or [])][:24]}
        scoped.append(safe)
matches=[c for c in cards if str(c.get("vendorCode") or "")==SKU]
same_case=[c for c in cards if str(c.get("vendorCode") or "").casefold()==SKU.casefold()]
pr=[x for x in prices if str(x.get("vendorCode") or "").casefold()==SKU.casefold()]
result={"read_only":True,"scanned_utc":datetime.now(timezone.utc).isoformat(),
"cards_total":len(cards),"prices_total":len(prices),"exact_existing_count":len(matches),"casefold_existing_count":len(same_case),
"price_rows":pr,"related_cards":scoped,"master":{"sku":SKU,"title":r[5],"retail_price_rub":r[7],"wb_price_rub":r[10],"stock":r[9],"weight_kg":r[22],"size_cm":[r[23],r[24],r[25]],"enabled_on_wb":r[17]}}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
print("SCAN_REPORT",json.dumps({"exact":len(matches),"casefold":len(same_case),"related":len(scoped),"master_price":r[7],"stock":r[9],"similar_codes":[c['vendorCode'] for c in scoped]},ensure_ascii=False),flush=True)
