"""Inspect Avito official XLSX template's allowed engine-category values read-only.
Do NOT publish an engine part with transmission-category fields. Do not alter
existing steady-state Avito autoload, which can archive omitted SKUs.
"""
import json,sys
from datetime import datetime,timezone
from pathlib import Path
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
KEYS=("двигател","масля","маслян","фильтр","трансмисс","вид запчасти","тип детали")
OUT=ROOT/"data/ea888_gen3_avito_category_audit_20261123_result.json"
def main():
    path=ROOT/"data/avito_template.xlsx"
    w=load_workbook(path,read_only=True,data_only=True)
    result={"source":"Avito official installed autoload workbook","read_only":True,
            "at":datetime.now(timezone.utc).isoformat(),"sheets":{}}
    for sheet in w:
        rows=[]
        for i,r in enumerate(sheet.iter_rows(values_only=True),1):
            if i<5 or any(key in " ".join(str(x or "").lower()[:250] for x in r[:120]) for key in KEYS):
                rows.append({"row":i,"values":[{"col":j+1,"value":str(v)[:150]} for j,v in enumerate(r[:120]) if v not in (None,"")][:16]})
            if len(rows)>90:break
        result["sheets"][sheet.title]={"row_total":sheet.max_row,"col_total":sheet.max_column,"sample_matches":rows}
    w.close()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"sheets":{k:{"rows":v["row_total"],"cols":v["col_total"],"matched":len(v["sample_matches"])} for k,v in result["sheets"].items()}},ensure_ascii=False))
if __name__=="__main__":main()
