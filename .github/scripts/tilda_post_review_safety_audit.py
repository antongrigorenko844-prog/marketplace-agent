import requests,json,re
from pathlib import Path
BASE="https://leoparts-akpp.ru"
UA={"User-Agent":"Mozilla/5.0"}
out={"mode":"READ_ONLY","checks":{},"products":[]}
for route in ["/","/catalog","/mehatronik-dq200"]:
 try:
  r=requests.get(BASE+route,headers=UA,timeout=20)
  parts=set(re.findall(r'storepart(?:uid|_uid|Uid)?["\\']?\\s*[:=]\\s*["\\']?(\\d{6,})',r.text,re.I))
  parts.update(re.findall(r'"storepart"\\s*:\\s*"(\\d+)"',r.text))
  recids=re.findall(r'id="rec(\\d+)"',r.text)
  out["checks"][route]={"status":r.status_code,"parts":list(parts),"recids":len(recids)}
  if r.status_code!=200:continue
  for part in parts:
   try:
    q=requests.get("https://store.tildaapi.com/api/getproductslist/",params={"storepartuid":part,"recid":recids[0] if recids else "","c":"","getparts":"true","getoptions":"true","slice":1,"size":100},headers=UA,timeout=25)
    j=q.json()
    out["checks"][route]["api_status"]=q.status_code
    out["checks"][route]["products_count"]=len(j.get("products") or [])
    for p in j.get("products") or []:
     if "0am325025h" in str(p.get("sku","")).lower() or "908157881413" in str(p.get("url","")) or "197861233143" in str(p.get("url","")):
      out["products"].append({"sku":p.get("sku"),"url":p.get("url"),"title":p.get("title"),"price":p.get("price"),"review_in_description":"Суетолог" in json.dumps(p,ensure_ascii=False),"uid":p.get("uid"),"fields":list(p.keys())})
   except Exception as e:out["checks"][route]["api_error"]=str(e)[:180]
 except Exception as e:out["checks"][route]={"error":str(e)[:180]}
Path("data/tilda_post_review_safety_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False))
