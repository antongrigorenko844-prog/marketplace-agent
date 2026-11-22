"""Read-only workbook layout check before recording the three owner-approved WB 6T30 SKUs."""
import json
from pathlib import Path
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/"data/master_control.xlsx"
OUT=ROOT/"data/leoparts_master_6t30_preflight_20261123.json"
target={"6t30","6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2","tool-dq200-acc-restore","tool-dsg-pressure-hp"}
b=load_workbook(BOOK,read_only=True,data_only=False)
result={"file":"data/master_control.xlsx","sheets":[]}
for sh in b:
    rows=sh.iter_rows(values_only=True)
    preview=[]
    for i,row in enumerate(rows,1):
        if i<=4:
            preview.append({"row_number":i,"columns":[{"index":j+1,"header":str(v)[:75]} for j,v in enumerate(row) if v is not None]})
    matches=[]
    for i,row in enumerate(sh.iter_rows(min_row=2,values_only=True),2):
        for j,v in enumerate(row):
            if v is not None and str(v).strip().casefold() in target:
                matches.append({"row_number":i,"sku_column":j+1,"sku":str(v).strip()})
                break
    result["sheets"].append({"name":sh.title,"max_row":sh.max_row,"max_col":sh.max_column,"top_rows":preview,"exact_matches":matches})
b.close()
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"sheets":[{"name":s["name"],"matches":s["exact_matches"]} for s in result["sheets"]]},ensure_ascii=False))
