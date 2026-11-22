"""Independent no-write quality and SEO audit of single LEOPARTS DQ200 landing."""
import json,re,time,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime,timezone
from html.parser import HTMLParser
URL="https://leoparts-akpp.ru/mehatronik-dq200"
OUT=Path("data/direct_recovery_20261122/landing_readiness.json")
class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.in_title=False;self.title="";self.h1=0;self.h2=0;self.desc="";self.canonical="";self.robots=""
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=="title":self.in_title=True
        if tag=="h1":self.h1+=1
        if tag=="h2":self.h2+=1
        if tag=="meta" and d.get("name","").lower()=="description":self.desc=d.get("content","")
        if tag=="meta" and d.get("name","").lower()=="robots":self.robots=d.get("content","")
        if tag=="link" and d.get("rel","").lower()=="canonical":self.canonical=d.get("href","")
    def handle_endtag(self,tag):
        if tag=="title":self.in_title=False
    def handle_data(self,data):
        if self.in_title:self.title+=data
def now():return datetime.now(timezone.utc).isoformat()
def run():
    o={"scope":"read_only_single_landing","url":URL,"started_utc":now(),"checks":[],"external_mutations":0}
    for i in range(3):
        item={"attempt":i+1,"time_utc":now()}
        try:
            request=urllib.request.Request(URL,headers={"User-Agent":"Mozilla/5.0 LEOPARTS-readonly-landing-audit"},method="GET")
            with urllib.request.urlopen(request,timeout=28) as resp:
                raw=resp.read(1200000)
                html=raw.decode("utf-8","replace")
                page=Page();page.feed(html)
                item.update(http_status=resp.status,bytes=len(raw),title=page.title[:180],
                    meta_description=page.desc[:240],canonical=page.canonical[:220],
                    robots=page.robots[:140],h1_count=page.h1,h2_count=page.h2,
                    anchor_bez_platy=bool(re.search(r'["\u0027#]bez-platy\b',html,re.I)),
                    anchor_s_platoi=bool(re.search(r'["\u0027#]s-platoi\b',html,re.I)),
                    anchor_platy=bool(re.search(r'["\u0027#]platy\b',html,re.I)),
                    vin_form_marker=("5246286101" in html),
                    cart_form_marker=("5260805101" in html),
                    metric_counter_marker=("113839860" in html),
                    benefit_block_phrase=("не просто восстановлен" in html.lower()),
                    html_has_dq200=("dq200" in html.lower()))
        except urllib.error.HTTPError as e:item.update(http_status=e.code);e.close()
        except Exception as e:item.update(error_type=type(e).__name__)
        o["checks"].append(item)
        if i<2:time.sleep(2)
    healthy=any(x.get("http_status")==200 and x.get("html_has_dq200") for x in o["checks"])
    o["status"]="available_at_least_once" if healthy else "landing_access_unverified"
    o["finished_utc"]=now()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Single page landing audit",o["status"],[x.get("http_status") for x in o["checks"]],flush=True)
if __name__=="__main__":run()
