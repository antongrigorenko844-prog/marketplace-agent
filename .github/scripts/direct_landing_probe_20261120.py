"""Read-only check: currently advertised LEOPARTS public landing pages.
No seller credentials or account API calls. No redirects outside allowlisted site.
"""
import concurrent.futures, datetime, json, re, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
SRC=Path("feeds/dq200_candidate.yml")
OUT=Path("data/direct_audit_20261120/site_probe.json")
URLS=["https://leoparts-akpp.ru/mehatronik-dq200",
      "https://leoparts-akpp.ru/mehatronik-dq200#bez-platy",
      "https://leoparts-akpp.ru/mehatronik-dq200#s-platoi"]
products=[]
try:
    raw=SRC.read_text(encoding="utf-8")
    root=ET.fromstring(raw)
    for offer in root.findall(".//offer"):
        link=offer.findtext("url")
        if link and link not in URLS:URLS.append(link)
        products.append({"id":offer.get("id"),"price":offer.findtext("price"),"available":offer.get("available"),"url":link})
except Exception as e:products=[{"source_error":type(e).__name__}]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a):return None
opener=urllib.request.build_opener(NoRedirect())
def check(url):
    u=urlsplit(url)
    result={"url":url,"checked_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if u.scheme!="https" or u.hostname not in ("leoparts-akpp.ru","www.leoparts-akpp.ru") or u.username or u.password or u.port not in (443,None):
        result["status"]="skipped_not_allowlisted";return result
    uri="https://"+u.hostname+u.path
    try:
        request=urllib.request.Request(uri,headers={"User-Agent":"Mozilla/5.0 (compatible; LEOPARTS-audit/1.0)"},method="GET")
        with opener.open(request,timeout=18) as response:
            result["http_status"]=response.status
            page=response.read(1000000).decode("utf-8","replace")
            result["bytes_read"]=len(page)
            result["contains_dq200"]=any(x in page.lower() for x in ("dq200","дсг7","мехатрон","dsg7"))
            if u.fragment:
                target=re.escape(u.fragment)
                result["target_anchor_found"]=bool(re.search(r'(?:id|name|data-anchor|data-record-anchor)\s*=\s*[\'"]'+target+r'[\'"]',page,re.I) or ('href="#'+u.fragment+'"') in page)
            result["possible_placeholder"]=any(x in page.lower() for x in ("t.me/username","wa.me/71234567890","renew subscription"))
    except urllib.error.HTTPError as e:result["http_status"]=e.code;e.close()
    except Exception as e:result["error_type"]=type(e).__name__
    return result
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results=list(pool.map(check,URLS))
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps({"created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "scope":"public_site_get_only","landing_pages_checked":len(results),"offers_in_feed":products,
 "urls":results},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("Public site read-only probe results",[(u.get("url","")[-25:],u.get("http_status"),u.get("target_anchor_found")) for u in results])
