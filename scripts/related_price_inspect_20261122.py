"""Read-only introspection of LEOPARTS price sheets with Python stdlib."""
from pathlib import Path
import json, zipfile, re, posixpath, xml.etree.ElementTree as ET
MAIN="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ODREL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKGREL="http://schemas.openxmlformats.org/package/2006/relationships"
def parse(path):
 with zipfile.ZipFile(path) as z:
  strings=[]
  if "xl/sharedStrings.xml" in z.namelist():
   root=ET.fromstring(z.read("xl/sharedStrings.xml"))
   strings=["".join(t.text or "" for t in si.iter(f"{{{MAIN}}}t")) for si in root.findall(f"{{{MAIN}}}si")]
  wb=ET.fromstring(z.read("xl/workbook.xml"))
  rels=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
  targets={r.attrib.get("Id"):r.attrib.get("Target") for r in rels if r.tag.endswith("}Relationship")}
  sheets=[]
  for sh in wb.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet"):
   name=sh.attrib["name"]; target=targets.get(sh.attrib.get(f"{{{ODREL}}}id"))
   if not target:continue
   target=target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/"+target)
   if target not in z.namelist(): continue
   root=ET.fromstring(z.read(target))
   rows=[]
   for row in root.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row"):
    line={}
    for c in row.findall(f"{{{MAIN}}}c"):
     ref=c.attrib.get("r","")
     x=re.match(r"[A-Z]+",ref)
     if not x:continue
     col=0
     for a in x.group():col=col*26+ord(a)-64
     t=c.attrib.get("t","")
     v=c.find(f"{{{MAIN}}}v")
     raw=v.text if v is not None else None
     inline=c.find(f"{{{MAIN}}}is")
     if t=="s" and raw is not None:
      try: val=strings[int(raw)]
      except:val=raw
     elif t=="inlineStr" and inline is not None:val="".join(x.text or "" for x in inline.iter(f"{{{MAIN}}}t"))
     elif t=="b":val=raw=="1"
     elif raw is not None:
      try:val=float(raw) if "." in raw or "E" in raw else int(raw)
      except:val=raw
     else: continue
     line[col-1]=val
    if line:
     mx=min(max(line)+1,100)
     rows.append([line.get(i) for i in range(mx)])
   sheets.append({"name":name,"rows":rows})
 return sheets
def main():
 info={}
 paths=["data/master_control.xlsx","data/kits.xlsx","data/kits-1.xlsx",
        "data/ozon_catalog.xlsx","data/wb_catalog.xlsx","data/avito_feed.xlsx",
        "data/avito_site_price_overrides.xlsx","data/yandex_market_offers.xlsx","data/price_audit.xlsx"]
 for p in paths:
  entry={"size":Path(p).stat().st_size,"sheets":[]}
  for sheet in parse(p):
   rows=sheet["rows"]
   entry["sheets"].append({"name":sheet["name"],"row_count":len(rows),
           "head":rows[:6],"sample_end":rows[-2:],
           "sample_skus":[r[:6] for r in rows if r and isinstance(r[0],str) and r[0].lower() in 
             ("0am325091e-set","0am325091f-set","klipsy-shtokov-2sht","dq500","0am325025h","0am325025x","plita-filtr-pylniki-sal-orig","0am325025","wht001922")][:15]})
  info[p]=entry
 out=Path("data/related_price_audit_20261122/schema_inspection.json");out.parent.mkdir(exist_ok=True)
 out.write_text(json.dumps(info,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
 print("SCHEMA_CHECK_OK",json.dumps({k:[(s["name"],s["row_count"]) for s in v["sheets"]] for k,v in info.items()},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
