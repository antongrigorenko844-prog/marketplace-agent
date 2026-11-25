"""Supplement to verified traffic snapshot: no external mutations, no PII.
Direct hasSearchVolume, campaign-attributed aggregate ecommerce, query targeting
categories, safe candidate dry-run. Does not use Wordstat's stale Nov data.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from decimal import Decimal
from collections import defaultdict,Counter
import json,re,os,urllib.request,urllib.error,hashlib,copy
import direct_performance_deep_20261125 as deep
import direct_preflight_20261120 as api
import direct_metrika_read_20261120 as mt
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import direct_commercial_evidence as commercial

CID=716316525
FID=4061297
COUNTER=113839860
BASE=Path("data/direct_traffic_quality/2026-11-26-45703549754")
CHANGE=deep.TODAY.fromisoformat("2026-11-25")
REGISTRY=BASE/"query_registry.json"
OEMS=["0am927769"+x for x in "egk"]
CANDIDATES=[(5826320196,x+" купить") for x in OEMS]+[(5826183946,"мехатроник с платой "+x) for x in OEMS]
METRICS=["ym:s:visits","ym:s:ecommercePurchases","ym:s:ecommerceRUBConvertedRevenue","ym:s:goal682257540visits","ym:s:goal675487735visits"]
def load(p):return json.loads(p.read_text(encoding="utf-8"))
def dump(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def norm(s):return re.sub(r"\s+"," ",str(s).casefold().replace("ё","е")).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def h(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
def amt(s):
    x=deep.num(s)
    return x if x is not None else Decimal(0)
def negatives_lexical(phrase,negatives):
    # Not a morphological Direct emulator. A broad token subset is only a
    # candidate conflict. Quoted negatives use an exact token count here.
    def words(q):return re.findall(r"[0-9a-zа-я]+",norm(q))
    tokens=Counter(words(phrase));out=[]
    for neg in negatives:
        need=Counter(words(neg))
        if not need:continue
        if '"' in neg and sum(need.values())!=sum(tokens.values()):continue
        if all(tokens[k]>=v for k,v in need.items()):out.append(neg)
    return out
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
def forecast(groups):
    gids={x[0] for x in CANDIDATES}
    regions={tuple(g["RegionIds"]) for g in groups if g["Id"] in gids}
    if len(regions)!=1:raise ValueError("Candidate regions differ")
    reg=list(next(iter(regions)));phrases=[x[1] for x in CANDIDATES]
    token=os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Direct OAuth missing")
    req={"method":"hasSearchVolume","params":{"SelectionCriteria":{"Keywords":phrases,"RegionIds":reg},"FieldNames":["Keyword","RegionIds","AllDevices","MobilePhones","Tablets","Desktops"]}}
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request("https://api.direct.yandex.com/json/v5/keywordsresearch",data=json.dumps(req,ensure_ascii=False).encode(),headers=headers,method="POST"),timeout=60) as res:raw=res.read(1000001)
    if len(raw)>1000000:raise ValueError("Oversize response")
    d=json.loads(raw)
    if d.get("error"):raise RuntimeError(api.scrub(d["error"]))
    rows=d.get("result",{}).get("HasSearchVolumeResults",[])
    if Counter(x.get("Keyword") for x in rows)!=Counter(phrases):raise ValueError("Forecast phrase coverage differs")
    if any(Counter(x.get("RegionIds",[]))!=Counter(reg) for x in rows):raise ValueError("Forecast region differs")
    return {"regions":reg,"rows":rows,"note":"YES/NO forecast, not search frequency or purchase forecast"}
def query_metrika(name,dimensions,metrics,filters=None,sort=None):
    p={"ids":str(COUNTER),"date1":"2026-11-16","date2":deep.TODAY.isoformat(),"dimensions":dimensions,"metrics":",".join(metrics),"accuracy":"full","limit":"100","lang":"ru"}
    if filters:p["filters"]=filters
    if sort:p["sort"]=sort
    raw=mt.call("/stat/v1/data",p);clean=mt.sanitize_stats(raw)
    clean["api_total_rows"]=raw.get("total_rows")
    clean["truncated"]=isinstance(raw.get("total_rows"),int) and raw["total_rows"]>len(clean["rows"])
    return {"label":name,"dimensions":dimensions,"metrics":metrics,"filter":filters,"data":clean}
def selftest():
    assert negatives_lexical("0am927769g купить",['"купить"'])==[]
    assert negatives_lexical("0am927769g купить",['купить'])==['купить']
    assert negatives_lexical("мехатроник с платой 0am927769e",['"мехатроник с платой 0am927769e"'])!=[]
    assert len(set(CANDIDATES))==6
    return 4
def run():
    rid=os.getenv("GITHUB_RUN_ID","local")
    if not re.fullmatch(r"[0-9]+|local",rid):raise ValueError("Bad run ID")
    out=Path("data/direct_traffic_quality")/(deep.TODAY.isoformat()+"-"+rid);out.mkdir(parents=True,exist_ok=True)
    result={"mode":"strict_read_only_supplement","campaign_id":CID,"counter_id":COUNTER,"feed_id":FID,"started_utc":deep.stamp(),"source":str(BASE),"external_mutations":0,"test_count":selftest(),"sections":{},"errors":{}}
    def go(k,fn):
        try:result["sections"][k]={"status":"ok","data":fn()}
        except Exception as e:result["sections"][k]={"status":"error","error":api.scrub(e)};result["errors"][k]=api.scrub(e)
        dump(out/"targeted_supplement.json",result)
    cfg={}
    def config():
        api.ALLOWED=tuple(set(api.ALLOWED)|{"ads","feeds"})
        cfg["campaign"]=api.campaign();cfg["groups"]=api.groups();cfg["keywords"]=api.keywords()
        cfg["ads"]=api.get("ads",{"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status"],"ResponsiveAdFieldNames":["Titles","Texts","Href","SitelinkSetId"],"ShoppingAdFieldNames":["FeedId","SitelinkSetId","FeedFilterConditions"],"Page":{"Limit":1000}},"Ads")
        cfg["feed"]=api.get("feeds",{"SelectionCriteria":{"Ids":[FID]},"FieldNames":["Id","Name","SourceType","Status","UpdatedAt","NumberOfItems","TitleAndTextSources"],"UrlFeedFieldNames":["Url"]},"Feeds")
        return {"campaign":cfg["campaign"],"groups":cfg["groups"],"manual_keywords":sum(x["Keyword"]!="---autotargeting" for x in cfg["keywords"]),"protected_fingerprints":{k:h(v) for k,v in cfg.items()},"production_feed_sha256":sha(Path("feeds/dq200_candidate.yml"))}
    go("config_before",config)
    go("forecast",lambda:forecast(cfg["groups"]))
    def ecommerce():
        defs=[
            ("by_source_day","ym:s:date,ym:s:lastTrafficSource",None),
            ("advertising_engine","ym:s:lastAdvEngine,ym:s:date","ym:s:lastTrafficSource=='ad'"),
            ("by_direct_campaign",commercial.DIMENSION,commercial.FILTER),
            ("exact_direct_campaign_by_day","ym:s:date","ym:s:lastDirectClickOrder==716316525"),
            ("exact_direct_campaign_by_group","ym:s:lastDirectBannerGroup","ym:s:lastDirectClickOrder==716316525"),
            ("utm_campaign_day","ym:s:date,ym:s:lastUTMCampaign","ym:s:lastTrafficSource=='ad'"),
            ("exact_utm_by_day","ym:s:date","ym:s:lastUTMCampaign=='dq200_feed_search'"),
        ]
        ret={}
        for name,dim,fil in defs:
            try:ret[name]={"status":"ok",**query_metrika(name,dim,METRICS,fil)}
            except Exception as e:ret[name]={"status":"error","error":api.scrub(e)}
        # Same source for purchased products. Product IDs are public catalog
        # identifiers; do not retrieve purchase IDs, ClientId, Yclid or raw visits.
        for name,fil in [("advertising_products","ym:s:lastTrafficSource=='ad'"),("direct_campaign_products","ym:s:lastDirectClickOrder==716316525")]:
            try:ret[name]={"status":"ok",**query_metrika(name,"ym:s:productID,ym:s:productName",["ym:s:productPurchasedQuantity","ym:s:productPurchasedRUBConvertedPrice"],fil)}
            except Exception as e:ret[name]={"status":"error","error":api.scrub(e)}
        return ret
    go("purchase_attribution",ecommerce)
    def exact_commercial():
        x=result["sections"]["purchase_attribution"]["data"]["by_direct_campaign"]
        if x["status"]!="ok":raise ValueError("Exact campaign report unavailable")
        d=x["data"]
        return commercial.commercial_summary(d["rows"],d["api_total_rows"],d["sampled"],commercial.LAUNCH_DATE,deep.TODAY)
    go("commercial_evidence",exact_commercial)
    def categories():
        rows=[];errs=[];lo=deep.TODAY.fromisoformat("2026-11-16")
        while lo<=deep.TODAY:
            hi=min(lo+timedelta(days=6),deep.TODAY)
            try:
                r=deep.report(lo,hi,"SEARCH_QUERY_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","AdId","Query","CriterionType","CriterionId","TargetingCategory","MatchType","Impressions","Clicks","Cost","Conversions"],"target-category-"+lo.isoformat())
                rows.extend(x for x in r if amt(x["Cost"])>0 or amt(x["Clicks"])>0)
            except Exception as e:errs.append({"range":[str(lo),str(hi)],"error":api.scrub(e)})
            lo=hi+timedelta(days=1)
        dump(out/"targeting_category_paid.json",{"rows":rows,"errors":errs})
        def sums(xs):
            return {"cost":str(sum(amt(x["Cost"]) for x in xs)),"clicks":int(sum(amt(x["Clicks"]) for x in xs)),"rows":len(xs)}
        by=defaultdict(list)
        prior={norm(x["Query"]):x["intent"] for x in load(REGISTRY)["rows"]}
        for x in rows:
            key=(x["AdGroupId"],x["CriterionType"],x.get("TargetingCategory"),x.get("MatchType"))
            by[key].append(x)
        summary=[]
        for key,xs in by.items():
            subset=[x for x in xs if prior.get(norm(x["Query"])) in {"non_target","service_explicit","outside_campaign_product"}]
            summary.append({"group_id":key[0],"criterion":key[1],"category":key[2],"match_type":key[3],**sums(xs),"potential_mismatch":sums(subset)})
        return {"coverage_complete":not errs,"errors":errs,"summary":summary,"row_count":len(rows),"note":"Historical settings changed; report category is actual click attribute when present."}
    go("targeting_categories",categories)
    def plan():
        forecast_rows={x["Keyword"]:x for x in result["sections"].get("forecast",{}).get("data",{}).get("rows",[])}
        ks=cfg["keywords"];groups={x["Id"]:x for x in cfg["groups"]}
        cn=(cfg["campaign"].get("NegativeKeywords") or {}).get("Items",[])
        # Read protected inventory from existing, verified feed snapshot.
        import xml.etree.ElementTree as ET
        offers={x.attrib["id"]:x for x in ET.parse("feeds/dq200_candidate.yml").findall(".//offer")}
        if len(offers)!=12:raise ValueError("Feed identity differs")
        outrows=[];mock=copy.deepcopy(ks)
        for gid,phrase in CANDIDATES:
            group=groups[gid];oem=phrase.split()[-1] if gid==5826183946 else phrase.split()[0]
            sku=oem if gid==5826320196 else "0am325025hx"+oem[-1]
            offer=offers[sku]
            stock=int(offer.findtext("count"))
            exact=[x for x in ks if norm(x["Keyword"])==norm(phrase)]
            same_oem=[{"id":str(x["Id"]),"group":x["AdGroupId"],"keyword":x["Keyword"]} for x in ks if oem in norm(x["Keyword"])]
            conflicts=negatives_lexical(phrase,cn+(group.get("NegativeKeywords") or {}).get("Items",[]))
            pred=forecast_rows.get(phrase,{}).get("AllDevices")
            eligible=bool(not exact and not conflicts and stock>0 and offer.attrib.get("available")=="true" and pred=="YES")
            row={"group_id":gid,"phrase":phrase,"sku":sku,"stock":stock,"exact_duplicates":len(exact),"existing_oem_keywords":same_oem,"lexical_conflicts":conflicts,"forecast_all_devices":pred,"status":"READY_FOR_OWNER_REVIEW_NOT_APPLIED" if eligible else "MONITORING_OR_BLOCKED","reason":"Existing OEM phrases can already cover these queries; test incremental benefit, do not imply missing traffic."}
            outrows.append(row)
            if eligible:mock.append({"CampaignId":CID,"AdGroupId":gid,"Keyword":phrase,"SIMULATED":True})
        assert mock[:len(ks)]==ks and len(mock)-len(ks)<=6
        return {"mode":"in_memory_dry_run_only","rows":outrows,"old_manual_count":sum(x["Keyword"]!="---autotargeting" for x in ks),"simulated_additions":len(mock)-len(ks),"existing_keywords_unchanged":mock[:len(ks)]==ks,"approval_required":True,"no_payload_dispatched":True,"rollback":"If later approved, save returned keyword IDs; rollback only those IDs after verifying their exact identity, never restore a stale full campaign.","wordstat":{"status":"BLOCKED_FRESH_STATISTICS","official":"https://aistudio.yandex.ru/ru/docs/search-api/concepts/wordstat","latest_available_date":"2026-10-07","note":"November frequency unavailable in current documented API. Do not treat cached numbers as November demand."}}
    go("candidate_dry_run",plan)
    def verify():
        c=api.campaign();g=api.groups();k=api.keywords()
        ads=api.get("ads",{"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status"],"ResponsiveAdFieldNames":["Titles","Texts","Href","SitelinkSetId"],"ShoppingAdFieldNames":["FeedId","SitelinkSetId","FeedFilterConditions"],"Page":{"Limit":1000}},"Ads")
        feed=api.get("feeds",{"SelectionCriteria":{"Ids":[FID]},"FieldNames":["Id","Name","SourceType","Status","UpdatedAt","NumberOfItems","TitleAndTextSources"],"UrlFeedFieldNames":["Url"]},"Feeds")
        return {"campaign_same":h(c)==h(cfg["campaign"]),"groups_same":h(g)==h(cfg["groups"]),"keywords_same":h(k)==h(cfg["keywords"]),"ads_same":h(ads)==h(cfg["ads"]),"feed_same":h(feed)==h(cfg["feed"]),"feed_file_same":sha(Path("feeds/dq200_candidate.yml"))==result["sections"]["config_before"]["data"]["production_feed_sha256"]}
    go("protection_readback",verify)
    result["finished_utc"]=deep.stamp();result["status"]="partial" if result["errors"] or any(x["status"]=="error" for x in result["sections"].get("purchase_attribution",{}).get("data",{}).values()) or not result["sections"].get("targeting_categories",{}).get("data",{}).get("coverage_complete",False) else "complete"
    dump(out/"targeted_supplement.json",result)
    print(json.dumps({"status":result["status"],"errors":result["errors"],"out":str(out)},ensure_ascii=False))
if __name__=="__main__":run()
