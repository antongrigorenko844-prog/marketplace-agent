import json,requests,re,html
from pathlib import Path
u="https://leoparts-akpp.ru/mehatronik-dq200/tproduct/908157881413-mehatronik-dsg7-dq200-0am0cw-proveren-na"
res={"url":u,"sku":"0AM325025H"}
try:
 r=requests.get(u,timeout=30,headers={"User-Agent":"Mozilla/5.0"})
 res["status"]=r.status_code;res["final_url"]=r.url
 t=r.text
 for key in ["0AM325025H","0am325025H","Суетолог","лето на нём","908157881413","197861233143"]:
  res[key]=key in t
 res["title"]=re.search(r"<title[^>]*>(.*?)</title>",t,re.S|re.I).group(1) if re.search(r"<title[^>]*>(.*?)</title>",t,re.S|re.I) else ""
 res["html_length"]=len(t)
except Exception as e:res["error"]=str(e)
Path("data/tilda_review_live_audit.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(res,ensure_ascii=False))
