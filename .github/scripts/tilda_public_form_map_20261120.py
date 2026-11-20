"""Read-only audit of Tilda public form identifiers on published LEOPARTS pages.
No submissions or CMS changes; no private information or full HTML stored.
"""
from html.parser import HTMLParser
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import json,re,time,urllib.request,urllib.error

OUT=Path("data/direct_optimization_20261120/tilda_public_form_map.json")
URLS=[
 "https://leoparts-akpp.ru/mehatronik-dq200",
 "https://www.leoparts-akpp.ru/mehatronik-dq200",
 "https://leoparts-akpp.ru/",
 "https://leoparts-akpp.ru/mehatronik-dq200/",
]
class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms=[]
        self.inform=False
        self.cur=None
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=="form":
            self.inform=True
            self.cur={"id":d.get("id"),"class":d.get("class"),
                "tilda_form_id":d.get("data-tilda-form-id"),
                "inputs":[]}
        elif self.inform and tag in ("input","textarea","select") and self.cur is not None:
            self.cur["inputs"].append({"type":d.get("type","text"),
                "name":(d.get("name") or "")[:80],
                "placeholder":(d.get("placeholder") or "")[:70]})
    def handle_endtag(self,tag):
        if tag=="form" and self.inform and self.cur is not None:
            if len(self.forms)<60:self.forms.append(self.cur)
            self.cur=None;self.inform=False
def utc():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    parts=urlsplit(url)
    if parts.scheme!="https" or parts.hostname not in ("leoparts-akpp.ru","www.leoparts-akpp.ru"):
        raise RuntimeError("Out of allowlist")
    class Block(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            u=urlsplit(newurl)
            if u.scheme=="https" and u.hostname in ("leoparts-akpp.ru","www.leoparts-akpp.ru"):
                return urllib.request.Request(newurl,headers=req.headers)
            return None
    opener=urllib.request.build_opener(Block())
    errors=[]
    for attempt in range(1,4):
        try:
            req=urllib.request.Request(url,method="GET",headers={
                "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"})
            with opener.open(req,timeout=20) as res:
                html=res.read(2000000).decode("utf-8","replace")
                frm=Forms();frm.feed(html)
                numbered=sorted(set(re.findall(r"(?i)(?:tilda/form|tilda-form|form)[_/\-]?(\d{7,12})",html)))
                specids=[str(x) for x in (5171350301,5246286101,5260805101)]
                matches={x:html.find(x)>=0 for x in specids}
                import html as html_tools
                def context_for(number):
                    found=list(re.finditer(re.escape(str(number)),html))
                    context=[]
                    for item in found[:4]:
                        snippet=html_tools.unescape(html[max(0,item.start()-160):min(len(html),item.end()+160)])
                        snippet=re.sub(r"<[^>]+>"," ",snippet)
                        snippet=re.sub(r"[\\w.+-]+@[\\w.-]+","[email]",snippet)
                        snippet=re.sub(r"(?:\\+?\\d[\\d\\s()\u2013-]{9,}\\d)","[phone]",snippet)
                        snippet=re.sub(r"\\s+"," ",snippet).strip()
                        context.append(snippet[:240])
                    return context
                contexts={key:context_for(key) for key in specids}
                return {"status":"ok","http":res.status,"length":len(html),
                        "form_id_context":contexts,"forms_count":len(frm.forms),
                        "forms":frm.forms,"virtual_form_identifiers":numbered[:80],
                        "tracked_form_id_mentions":matches,"tilda_marker":"tilda" in html.lower()}
        except urllib.error.HTTPError as e:
            errors.append({"attempt":attempt,"http":e.code});e.close()
        except Exception as e:
            errors.append({"attempt":attempt,"exception":type(e).__name__})
        if attempt<3:time.sleep(3)
    return {"status":"unavailable","attempts":errors}
def main():
    result={"audit":"tilda_published_public_only","started_utc":utc(),
        "form_id_interest":[5171350301,5246286101,5260805101],
        "paths":[],"no_form_submissions":True,"no_site_modifications":True}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    for url in URLS:
        result["paths"].append({"url":url,**fetch(url)})
        OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        if any(x["status"]=="ok" for x in result["paths"]) and url!=URLS[0]:
            break
    result["finished_utc"]=utc()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Tilda public form audit: ",[(x["url"],x["status"],x.get("forms_count")) for x in result["paths"]],flush=True)
if __name__=="__main__":main()
