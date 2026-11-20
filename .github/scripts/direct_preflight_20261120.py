"""Read-only live preflight for narrowly scoped DQ200 campaign search quality.
Do not mutate Yandex Direct. Reads only one campaign, its keyword/group inventory,
current-day Direct Reports and optional Metrika read permission.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
BASE="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_optimization_20261120/preflight.json")
DAY="2026-11-20"
CANDIDATES=('"02e мехатроник в сборе"','"ремонт дсг вологда"')
PROTECTED=("мехатроник dq200","мехатроник dsg","мехатроник с платой","dq200 купить","0am325025","0am927769")
ALLOWED=("campaigns","keywords","adgroups","reports")
def now():
    return datetime.now(timezone.utc).isoformat()
def scrub(msg):
    s=str(msg)
    for k,v in os.environ.items():
        if len(v)>8 and any(w in k for w in ("TOKEN","API_KEY","SECRET","PASSWORD")):
            s=s.replace(v,"[redacted]")
    return s[:420]
def save(r):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def request(service,method,params,reports=False):
    if service not in ALLOWED or (service=="reports" and (not reports or method!="read")) or (service!="reports" and (reports or method!="get")):
        raise RuntimeError("Outside read-only API allowlist")
    token=os.environ.get("YANDEX_DIRECT_TOKEN","")
    if not token:raise RuntimeError("Missing Direct OAuth in workflow secrets")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    login=os.environ.get("YANDEX_DIRECT_LOGIN","")
    if login:headers["Client-Login"]=login
    if reports:headers.update({"processingMode":"auto","returnMoneyInMicros":"false","skipReportHeader":"true","skipColumnHeader":"false","skipReportSummary":"true"})
    body={"params":params} if reports else {"method":method,"params":params}
    req=urllib.request.Request(BASE+service,method="POST",headers=headers,data=json.dumps(body,ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req,timeout=65) as resp:
            content=resp.read(2*1024*1024+1)
            if len(content)>2*1024*1024:raise RuntimeError("Oversize API response")
            return resp.status,content.decode("utf-8-sig"),dict(resp.headers)
    except urllib.error.HTTPError as e:
        code=e.code
        e.close()
        if reports and code in (201,202):return code,"",{}
        raise RuntimeError(service+" HTTP "+str(code)) from None
def get(service,params,key):
    items=[]
    p=dict(params)
    for _ in range(15):
        status,raw,_=request(service,"get",p)
        if status!=200:raise RuntimeError("Invalid read status")
        d=json.loads(raw)
        if d.get("error"):raise RuntimeError(service+" "+scrub(d["error"]))
        result=d.get("result") or {}
        batch=result.get(key)
        if not isinstance(batch,list):raise RuntimeError("Invalid "+service+" response")
        items.extend(batch)
        if "LimitedBy" not in result:return items
        cur=result["LimitedBy"]
        if not isinstance(cur,int):raise RuntimeError("Invalid pagination")
        p["Page"]={"Limit":1000,"Offset":cur}
    raise RuntimeError("Too many result pages")
def campaign():
    selection={"SelectionCriteria":{"Ids":[CID]},"FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
               "UnifiedCampaignFieldNames":["BiddingStrategy","CounterIds","PriorityGoals"],"Page":{"Limit":1000}}
    rows=get("campaigns",selection,"Campaigns")
    if len(rows)!=1 or rows[0].get("Id")!=CID or rows[0].get("Name")!=NAME:
        raise RuntimeError("Unexpected campaign identity")
    return rows[0]
def keywords():
    fields={"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","Status","ServingStatus"],"Page":{"Limit":1000}}
    rows=get("keywords",fields,"Keywords")
    if any(x.get("CampaignId")!=CID for x in rows):raise RuntimeError("Other-campaign keywords")
    return rows
def groups():
    fields={"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","Name","CampaignId","Status","NegativeKeywords","RegionIds"],"Page":{"Limit":1000}}
    rows=get("adgroups",fields,"AdGroups")
    if any(x.get("CampaignId")!=CID for x in rows):raise RuntimeError("Other-campaign groups")
    return rows
def report(kind,columns,tag):
    p={"SelectionCriteria":{"DateFrom":DAY,"DateTo":DAY,"Filter":[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]},
       "FieldNames":columns,"ReportName":"leoparts-preflight-"+tag+"-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
       "ReportType":kind,"DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES"}
    for n in range(1,13):
        status,raw,h=request("reports","read",p,True)
        if status==200:
            rdr=csv.DictReader(io.StringIO(raw),delimiter="\t")
            if not rdr.fieldnames or not {"Clicks","Cost","CampaignId"}.issubset(rdr.fieldnames):
                raise RuntimeError("Invalid report columns")
            rows=list(rdr)
            if any(x.get("CampaignId")!=str(CID) for x in rows):
                raise RuntimeError("Unexpected campaign in Report")
            return rows
        if status not in (201,202):raise RuntimeError("Invalid reports status")
        time.sleep(min(8,1+n))
    raise RuntimeError("Report timeout")
def num(s):
    if s is None or str(s).strip() in ("","--","-"):return None
    n=Decimal(str(s).replace(",","."))
    if n<0 or not n.is_finite():raise RuntimeError("Bad numeric metric")
    return n
def totals(rows):
    return {k:str(sum((num(x.get(k)) or Decimal(0) for x in rows),Decimal(0))) for k in ("Impressions","Clicks","Cost","Conversions")}
def normalize(s):
    return re.sub(r"\s+"," ",re.sub(r'["+\[\]!-]',' ',str(s).casefold())).strip()
def overlap(candidate,keyword):
    # Strict lexical approximation, not Yandex operator/lemmatization engine.
    a=set(normalize(candidate).split())
    b=set(normalize(keyword).split())
    return bool(a) and a.issubset(b)
def run():
    result={"status":"in_progress","audit_mode":"read_only","captured_utc":now(),"campaign_id":CID,
            "dates":DAY,"candidates":list(CANDIDATES),"external_mutations":0,"errors":[]}
    save(result)
    try:
        c=campaign()
        ks=keywords()
        gs=groups()
        existing=(c.get("NegativeKeywords") or {}).get("Items")
        if not isinstance(existing,list) or any(not isinstance(x,str) for x in existing):
            raise RuntimeError("Invalid negative phrase list")
        stamp=hashlib.sha256(json.dumps(existing,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
        manual=[x for x in ks if x.get("Keyword")!="---autotargeting"]
        auto=[x for x in ks if x.get("Keyword")=="---autotargeting"]
        candidates=[]
        for phrase in CANDIDATES:
            collided=[{"id":k.get("Id"),"keyword":k.get("Keyword"),"group_id":k.get("AdGroupId")}
                for k in manual if overlap(phrase,k.get("Keyword",""))]
            candidates.append({"phrase":phrase,"present_exact":phrase in existing,
               "lexical_keyword_overlap_approximation":collided,
               "phrase_word_count":len(normalize(phrase).split())})
        summary=report("CAMPAIGN_PERFORMANCE_REPORT",
             ["Date","CampaignId","Impressions","Clicks","Cost","Conversions"],"campaign")
        queries=report("SEARCH_QUERY_PERFORMANCE_REPORT",
             ["Date","CampaignId","AdGroupId","Query","CriterionType","Clicks","Cost","Conversions"],"queries")
        paid=defaultdict(lambda:{"Cost":Decimal(0),"Clicks":Decimal(0),"Conversions":Decimal(0),"criterion_type":""})
        for row in queries:
            q=(row["Query"],row["AdGroupId"])
            agg=paid[q]
            for k in ("Cost","Clicks","Conversions"):agg[k]+=num(row.get(k)) or Decimal(0)
            agg["criterion_type"]=row.get("CriterionType","")
        ranked=sorted(({"Query":q,"AdGroupId":g,"Cost":str(v["Cost"]),
                "Clicks":str(v["Clicks"]),"Conversions":str(v["Conversions"]),
                "CriterionType":v["criterion_type"]} for (q,g),v in paid.items() if v["Cost"]>0),
                key=lambda x:Decimal(x["Cost"]),reverse=True)
        result.update({"type":c["Type"],"state":c["State"],"status_provider":c["Status"],
            "negative_count":len(existing),"negative_fingerprint_sha256":stamp,
            "negatives":existing,"negative_changes_proposed":len(CANDIDATES),
            "groups":[{"id":x.get("Id"),"name":x.get("Name"),"group_negatives":len((x.get("NegativeKeywords") or {}).get("Items") or [])} for x in gs],
            "manual_keyword_count":len(manual),"autotargeting_condition_count":len(auto),
            "candidate_checks":candidates,"today_campaign_totals":totals(summary),
            "today_query_total_cost":str(sum((Decimal(v["Cost"]) for v in ranked),Decimal(0))),
            "today_query_paid":ranked[:100],"today_report_rows":len(queries),"live_read_utc":now(),
            "metrika_token_configured":bool(os.environ.get("YANDEX_METRIKA_TOKEN")),
            "weekly_limit_microrub":((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search",{}).get("HighestPosition",{}).get("WeeklySpendLimit")})
        ready=(c["Type"]=="UNIFIED_CAMPAIGN" and c["State"]=="ON" and len(existing)==130
            and len(gs)==6 and len(manual)==266 and len(auto)==6
            and all(not x["present_exact"] and not x["lexical_keyword_overlap_approximation"] for x in candidates))
        result["status"]="preflight_verified_safe_to_propose" if ready else "manual_review_required"
    except Exception as e:
        result["status"]="failed";result["errors"].append(scrub(e))
    result["completed_utc"]=now()
    save(result)
    print("Direct candidate preflight:",result["status"],"negatives",result.get("negative_count"),
          "today",result.get("today_campaign_totals"),flush=True)
    return 0 if result["status"]!="failed" else 1
if __name__=="__main__":sys.exit(run())
