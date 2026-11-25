"""Read-only DQ200 traffic audit. Reuse existing Direct/Metrica readers.
No ad/site/feed writes, no checkout, no Webvisor, no user-level logs.
Historical snapshots retained. New query classification is conservative and
reviewable; no automatic exclusions. Monetary figures incl VAT, RUB.
"""
import json,re,os,sys,hashlib
from collections import defaultdict,Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import direct_performance_deep_20261125 as deep
import direct_preflight_20261120 as api
import direct_metrika_read_20261120 as mt

BASE=Path("data/direct_audit_20261126")
OLD=Path("data/direct_performance_20261125/semantic_review.json")
FILTER=Path("data/direct_performance_20261125/exact_query_filter.json")
DRAFT=Path("drafts/direct_conversion_20261126/financial_scenarios.json")
CHANGE="2026-11-25"
CID=716316525
GOALS=[675487735,682257540,685988392,675272284,675506966,675507028,686020680]
METRICS=("Impressions","Clicks","Cost","Conversions")
COMMERCIAL={"hot_commercial","oem_commercial","specific_part_commercial","potential_commercial"}
CLEAR={"non_target","service_explicit","outside_campaign_product"}
AUTO="AUTOTARGETING"

def load(p):return json.loads(p.read_text(encoding="utf-8"))
def save(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def norm(s):
    return re.sub(r"\s+"," ",str(s).casefold().replace("ё","е")).strip()
def value(s):return deep.num(s)
def total(rows,key):
    numbers=[value(r.get(key)) for r in rows]
    return sum((v for v in numbers if v is not None),Decimal(0)) if any(v is not None for v in numbers) else None
def ratio(a,b,scale=1):
    return None if a is None or b is None or b==0 else round(float(a/b)*scale,4)
def amount(n):return None if n is None else float(n)
def aggregate(rows):
    t={k:total(rows,k) for k in METRICS}
    return {**{k:amount(v) for k,v in t.items()},"rows":len(rows),"CPC":ratio(t["Cost"],t["Clicks"]),"CTR_pct":ratio(t["Clicks"],t["Impressions"],100)}
def group(rows,fields,agg=aggregate):
    bins=defaultdict(list)
    for r in rows:bins[tuple(str(r.get(k,"")) for k in fields)].append(r)
    return [dict(zip(fields,key))|agg(v) for key,v in sorted(bins.items())]

# Manual refinements of the previous exact-text review. This is a triage of
# the query, not a forecast or proof of purchase intention or availability.
OVERRIDES={
"плата мехатроника dsg7 dq200 купить в спб":("hot_commercial","explicit DQ200 part and purchase"),
"мехатроник в коробке передач фольксваген тигуан цена":("potential_commercial","fitment must be confirmed"),
"мехатроник новый к сеат леон купить":("potential_commercial","condition and fitment must be confirmed"),
"пульт для проверки мехатроника дсг 7":("outside_campaign_product","tool, not an advertised mechatronic or board"),
"мехатроник ауди а3 цена":("potential_commercial","fitment must be confirmed"),
"в мехатроник какую жижу брать шкода октавия":("informational","fluid specification"),
"плата мехатроника dsg 7":("potential_commercial","part identified, fitment uncertain"),
"плата мехатроник дсг 7 dq200 купить китай":("hot_commercial","purchase, origin preference must be confirmed"),
"запчасть для коробки передач dsg 7 купить":("ambiguous","may be a relevant part, not specified"),
"0am325025h zdz мехатроник с прогр обеспеч":("ambiguous","exact OEM but software requirement unclear"),
"ремкомплект для мехатроника dsg 7 dq200 купить":("specific_part_commercial","LEOPARTS component family, not current feed offer"),
"запчасти для dsg-7":("ambiguous","possible relevant component"),
"ремонт акпп купить":("ambiguous","mixed service/purchase intent"),
"ремонт мехатроника сервисы":("service_explicit","explicit service search"),
"коробка робот замена":("ambiguous","could be replacement part or service"),
"встанет ли мехатроник 0aw вместо 0am":("diagnostic_possible","fitment question, potential purchase"),
"мехатроник октавия а7 1.4 артикул":("diagnostic_possible","part identification, possible purchase"),
"куда подходит мехатроник dq200":("diagnostic_possible","fitment question"),
"отличия платы мехатроника 0am927769d от 0am927769g":("diagnostic_possible","comparison of actual board revisions"),
"как выглядит гидравлический аккумулятор в мехатронике dq200":("diagnostic_possible","part identification"),
"плата мехатроника dsg 7 dq200 фото":("diagnostic_possible","part identification"),
"сколько стоит плата от машины":("non_target","generic automotive electronics, no DQ200 part"),
"автомобильная плата":("non_target","generic automotive electronics, no DQ200 part"),
"запчасти по вин номеру":("ambiguous","generic VIN search, no specific component"),
"поиск запчастей по vin коду автомобиля":("ambiguous","generic VIN search, no specific component"),
"купить запчасти по вину":("ambiguous","generic VIN search, no specific component"),
}
def previous_map():
    m=defaultdict(set)
    for r in load(OLD)["query_rows"]:m[norm(r["query"])].add(r["category"])
    return {k:next(iter(v)) for k,v in m.items() if len(v)==1}
def classify(query,prior):
    q=norm(query)
    if q in OVERRIDES:return (*OVERRIDES[q],"reviewed_exact_override")
    prev=prior.get(q)
    # Keep prior meaning; distinguish concrete service-provider intent from
    # generic repair of a specific component. Never blanket-exclude repair.
    if prev=="service":
        explicit=bool(re.search(r"(?:где\s|сервис|адрес|вологд|оренбург|калуг|краснодар|ставропол|пермь|с гарантией|стоимость ремонта|цен[аы] ремонта|ремонт.{0,24}цен[аы]|сколько стоит ремонт)",q))
        return ("service_explicit" if explicit else "diagnostic_possible","service search indicators" if explicit else "repair/replacement may imply part purchase","previous_review_refined")
    if prev=="informational":
        diagnostic=bool(re.search(r"ошибк|p17bf|01315|u010100|давлен|трещин|диагност|износ|резкое торможение",q))
        return ("diagnostic_possible" if diagnostic else "informational","fault/fitment may precede part purchase" if diagnostic else "information query","previous_review_refined")
    if prev=="other_parts":return ("outside_campaign_product","explicit non-feed product or other gearbox; stock not inferred","previous_review")
    if prev=="high_commercial":return ("oem_commercial","specific OEM and product","previous_review")
    if prev=="commercial":
        return ("hot_commercial" if re.search(r"купить|заказать|в наличии",q) else "specific_part_commercial","commercial wording; condition/fitment not guaranteed","previous_review")
    if prev in ("potential_commercial","ambiguous","non_target"):
        return (prev,"previous reviewed intent; verify fitment when unclear","previous_review")
    # Unknown queries deliberately remain reviewable. No automatic hard negatives.
    if re.search(r"0am927769[defgk]|0am325025[a-z]*",q) and re.search(r"купить|цена|плата|мехатроник",q):
        return ("oem_commercial","OEM wording; verify variant and intent","heuristic")
    if re.search(r"мехатрон|гидроблок|гидроплит",q) and re.search(r"купить|заказать|цен[ауы]|стоимост",q) and re.search(r"dq\s*200|дк[у]?\s*200|dsg\s*7|дсг\s*7",q) and not re.search(r"ремонт|диагност|замен|сняти|установ",q):
        return ("hot_commercial","explicit purchase/price and DQ200 family, manual review","heuristic")
    return ("ambiguous","new/unreviewed query, no exclusion","heuristic_review_required")
def triage(rows,prior):
    out=[]
    for r in rows:
        cat,reason,source=classify(r.get("Query",""),prior)
        out.append(r|{"intent":cat,"reason":reason,"classification_source":source,"profit":None,"estimated_purchase_probability":None})
    return out
def scope_stats(rows,detail_all=None):
    a=aggregate(rows)
    spend=total(rows,"Cost")
    def sub(cats):
        rr=[r for r in rows if r["intent"] in cats]
        return aggregate(rr)|{"share_of_detailed_spend_pct":ratio(total(rr,"Cost") or Decimal(0),spend,100)}
    commercial=sub(COMMERCIAL);clear=sub(CLEAR)
    commercial["cost_per_commercial_click"]=commercial["CPC"]
    a["commercial_or_potential"]=commercial;a["clear_mismatch_or_explicit_service"]=clear
    a["categories"]=group(rows,["intent"])
    a["diagnostic_or_ambiguous"]=sub({"diagnostic_possible","ambiguous","informational"})
    a["CTR_note"]="paid-row CTR is not campaign CTR; use full targeting or campaign report"
    return a
def windows(today,full_start):
    end=today-timedelta(days=1)
    out={str(n):(max(full_start,today-timedelta(days=n)).isoformat(),end.isoformat()) for n in (3,7,14,30)}
    out["all_available"]=(full_start.isoformat(),end.isoformat())
    out["intraday"]=(today.isoformat(),today.isoformat())
    return out
def selftest():
    p={"ремонт мехатроника":"service","где ремонтируют мехатроник":"service","p17bf ошибка dsg 7 dq200":"informational"}
    assert classify("ремонт мехатроника",p)[0]=="diagnostic_possible"
    assert classify("где ремонтируют мехатроник",p)[0]=="service_explicit"
    assert classify("p17bf ошибка dsg 7 dq200",p)[0]=="diagnostic_possible"
    assert classify("плата 0am927769k купить",{})[0]=="oem_commercial"
    assert classify("неизвестный запрос",{})[0]=="ambiguous"
    assert classify("купить запчасти по вину",{})[0]=="ambiguous"
    assert classify("ремкомплект для мехатроника dsg 7 dq200 купить",{})[0]=="specific_part_commercial"
    assert total([{"Cost":"--"}],"Cost") is None
    assert total([{"Cost":"1.23"},{"Cost":"2.01"}],"Cost")==Decimal("3.24")
    a=scope_stats(triage([{"Query":"неизвестный запрос","Cost":"10","Clicks":"1","Impressions":"5","Conversions":"0"}],{}))
    assert a["clear_mismatch_or_explicit_service"]["share_of_detailed_spend_pct"]==0
    return 10
def run():
    # Every run has a new path; older baseline never overwritten.
    rid=os.getenv("GITHUB_RUN_ID","local")
    if not re.fullmatch(r"[0-9]+|local",rid):raise ValueError("Invalid run ID")
    out=Path("data/direct_traffic_quality")/(deep.TODAY.isoformat()+"-"+rid)
    out.mkdir(parents=True,exist_ok=True)
    report={"mode":"strict_read_only","campaign_id":CID,"started_utc":deep.stamp(),"external_mutations":0,"test_count":selftest(),"sections":{},"errors":{}}
    def go(k,fn):
        try:report["sections"][k]={"status":"ok","data":fn()}
        except Exception as e:report["sections"][k]={"status":"error","error":api.scrub(e)};report["errors"][k]=api.scrub(e)
        save(out/"analysis.json",report)
    deep.ROOT=out;deep.OUT=out/"live_30d.json"
    deep.run()
    d=load(deep.OUT);s=d["sections"]
    def config():
        c=api.campaign();old=load(FILTER);actual=(c.get("NegativeKeywords") or {}).get("Items",[])
        ks=api.keywords()
        return {"negatives_count":len(actual),"five_present":all(x in actual for x in old["additions"]),"same_set_as_change":set(actual)==set(old["after"]),"missing":sorted(set(old["after"])-set(actual)),"added":sorted(set(actual)-set(old["after"])),"manual_keywords":sum(x["Keyword"]!="---autotargeting" for x in ks),"settings":s["campaign"]["data"],"approved_neutral_titles_policy":True}
    go("config",config)
    prior=previous_map()
    def query_detail():
        # Additional report with CriterionId + actual Criterion, separate goals.
        # Query totals can omit low-volume/hidden terms. Preserve reconciliation.
        rows=[];errors=[];chunks=[]
        lo=deep.START
        while lo<=deep.TODAY:
            hi=min(lo+timedelta(days=6),deep.TODAY)
            try:
                r=deep.report(lo,hi,"SEARCH_QUERY_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","Query","CriterionType","CriterionId","Criterion","MatchedKeyword",*METRICS],"query-goals-"+lo.isoformat(),goals=GOALS)
                paid=[x for x in r if (value(x.get("Cost")) or 0)>0 or (value(x.get("Clicks")) or 0)>0]
                rows.extend(paid);chunks.append({"from":lo.isoformat(),"to":hi.isoformat(),"rows":len(r),"paid_rows":len(paid)})
            except Exception as e:errors.append({"from":lo.isoformat(),"to":hi.isoformat(),"error":api.scrub(e)})
            lo=hi+timedelta(days=1)
        save(out/"query_goals.json",{"rows":rows,"chunks":chunks,"errors":errors,"attribution":"LC"})
        return {"rows":len(rows),"chunks":chunks,"errors":errors,"attribution":"LC","all_chunks_succeeded":not errors}
    go("query_goal_detail",query_detail)
    def traffic():
        raw=s["queries"]["data"]["paid_rows"];rows=triage(raw,prior)
        save(out/"query_registry.json",{"source_finished":d["finished_utc"],"rows":rows,"prior_review":str(OLD),"unknown_query_policy":"ambiguous by default; no writes"})
        result={}
        for label,(lo,hi) in windows(deep.TODAY,deep.START).items():
            r=[x for x in rows if lo<=x["Date"]<=hi]
            cd=[x for x in s["daily"]["data"]["by_date"] if lo<=x["Date"]<=hi]
            cost=total(cd,"Cost");detail=total(r,"Cost")
            result[label]={"from":lo,"to":hi,"campaign":aggregate(cd),"detail":scope_stats(r),"by_targeting":group(r,["CriterionType"],scope_stats),"by_group":group(r,["AdGroupId"],scope_stats),"by_group_targeting":group(r,["AdGroupId","CriterionType"],scope_stats),"unmatched_cost":amount(cost-detail) if cost is not None and detail is not None else None,"detail_coverage_pct":ratio(detail,cost,100)}
        after=[x for x in rows if x["Date"]>CHANGE and x["Date"]<deep.TODAY.isoformat()]
        change_date=deep.TODAY.fromisoformat(CHANGE)
        complete_after=max(0,(deep.TODAY-change_date).days-1)
        # Before/after windows use the frozen pre-change export when the
        # rolling report has moved past it; no zero-filled days outside launch.
        baseline=load(BASE/"live_30d.json")
        historical=triage(baseline["sections"]["queries"]["data"]["paid_rows"],prior)
        active_dates=[x["Date"] for x in baseline["sections"]["daily"]["data"]["by_date"] if (value(x.get("Cost")) or 0)>0]
        launch=min(active_dates) if active_dates else None
        comparisons={}
        for n in (3,7,14,30):
            b0=(change_date-timedelta(days=n)).isoformat();b1=(change_date-timedelta(days=1)).isoformat()
            a0=(change_date+timedelta(days=1)).isoformat();a1=(change_date+timedelta(days=n)).isoformat()
            before=[x for x in historical if b0<=x["Date"]<=b1]
            post=[x for x in rows if a0<=x["Date"]<=a1 and x["Date"]<deep.TODAY.isoformat()]
            ready=complete_after>=n and a0>=deep.START.isoformat()
            comparability=bool(launch and b0>=launch)
            status="MONITORING" if not ready else ("REVIEW" if comparability else "INSUFFICIENT_COMPARABLE_BASELINE")
            comparisons[str(n)]={"before_range":[b0,b1],"after_range":[a0,a1],"before":scope_stats(before),"after":scope_stats(post),"after_window_complete":ready,"before_active_coverage":comparability,"status":status}
        result["post_change"]={"full_days":complete_after,"status":"MONITORING" if complete_after<3 else "REVIEW","detail":scope_stats(after),"change_day_excluded":True,"comparisons":comparisons,"note":"No causal inference. 14/30-day pre-change comparison may include pre-launch dates and is flagged. Historical baseline can differ due to delayed attribution."}
        return {"row_count":len(rows),"distinct_queries":len(set(x["Query"] for x in rows)),"windows":result,"top_mismatch":[x for x in sorted(group([r for r in rows if r["intent"] in CLEAR],["Query","intent"]),key=lambda r:r["Cost"] or 0,reverse=True)[:20]],"method":"exact prior review and conservative refinements; new queries not automatically excluded"}
    go("traffic",traffic)
    def direct_criteria():
        rows=deep.report(deep.START,deep.TODAY,"CUSTOM_REPORT",["Date","CampaignId","AdGroupId","CriterionType",*METRICS],"targeting-full")
        save(out/"targeting_full.json",rows)
        return {label:{"from":lo,"to":hi,"by_targeting":group([r for r in rows if lo<=r["Date"]<=hi],["CriterionType"]),"by_group_targeting":group([r for r in rows if lo<=r["Date"]<=hi],["AdGroupId","CriterionType"])} for label,(lo,hi) in windows(deep.TODAY,deep.START).items()}
    go("targeting_full",direct_criteria)
    def direct_ads():
        r=deep.report(deep.START,deep.TODAY,"AD_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","AdId",*METRICS],"ad-detail")
        save(out/"ads_performance.json",r)
        return {"by_ad":group(r,["AdGroupId","AdId"]),"note":"ad ID not offer ID. No arbitrary per-SKU allocation."}
    go("ad_level",direct_ads)
    def ec():
        results={}
        base={"ids":str(deep.COUNTER),"date1":deep.START.isoformat(),"date2":deep.TODAY.isoformat(),"accuracy":"full","limit":"100","lang":"ru"}
        def query(name,dimensions,metrics,paid=False):
            params=base|{"dimensions":dimensions,"metrics":metrics}
            if paid:params["filters"]="ym:s:lastUTMCampaign=='dq200_feed_search'"
            try:
                raw=mt.call("/stat/v1/data",params)
                cleaned=mt.sanitize_stats(raw)
                cleaned["api_total_rows"]=raw.get("total_rows")
                cleaned["truncated"]=isinstance(raw.get("total_rows"),int) and raw["total_rows"]>len(cleaned["rows"])
                results[name]={"status":"ok","metrics":metrics.split(","),"dimensions":dimensions.split(","),"data":cleaned}
            except Exception as e:results[name]={"status":"error","error":api.scrub(e)}
        query("all_sources","ym:s:lastTrafficSource","ym:s:visits,ym:s:ecommercePurchases,ym:s:ecommerceRUBConvertedRevenue")
        query("paid_products","ym:s:productID,ym:s:productName","ym:s:productImpressions,ym:s:productBasketsQuantity,ym:s:productPurchasedQuantity",True)
        query("paid_products_name_fallback","ym:s:productName","ym:s:productImpressions,ym:s:productBasketsQuantity,ym:s:productPurchasedQuantity",True)
        query("paid_funnel","ym:s:isNewUser","ym:s:users,ym:s:productImpressionsUniq,ym:s:productBasketsUniq,ym:s:productBeginCheckoutUniq,ym:s:productPurchasedUniq",True)
        return results
    go("ecommerce",ec)
    def gaps():
        ks=s["keywords"]["data"]
        by={str(g["Id"]):g for g in s["groups"]["data"]}
        result=[]
        for v in "defgk":
            oem="0am927769"+v
            matched=[{"id":str(x["Id"]),"group":str(x["AdGroupId"]),"keyword":x["Keyword"],"state":x["State"]} for x in ks if oem in norm(x["Keyword"])]
            proposals=[("5826320196",oem+" купить"),("5826183946","мехатроник с платой "+oem)]
            result.append({"oem":oem,"existing":matched,"candidate_phrases":[{"group":gid,"keyword":p,"exists_literal":any(norm(x["Keyword"])==norm(p) and str(x["AdGroupId"])==gid for x in ks),"status":"MONITORING_REVIEW_BEFORE_APPLY"} for gid,p in proposals]})
        return {"variants":result,"note":"literal inventory not a full matching or demand forecast; keep neutral titles and all negatives"}
    go("semantic_gaps",gaps)
    go("economics",lambda:{"source":str(DRAFT),"model":load(DRAFT),"actual_paid_orders":None,"actual_ROAS":None,"actual_CAC":None,"note":"prior scenario assumptions, no evidence of actual paid orders or all variable expenses"})
    report["finished_utc"]=deep.stamp()
    report["status"]="partial" if report["errors"] or d["status"]!="complete" or not report["sections"].get("query_goal_detail",{}).get("data",{}).get("all_chunks_succeeded") or any(x.get("status")=="error" for x in report["sections"].get("ecommerce",{}).get("data",{}).values()) else "complete"
    save(out/"analysis.json",report)
    print(json.dumps({"status":report["status"],"output":str(out),"errors":report["errors"],"selftests":report["test_count"]},ensure_ascii=False))
if __name__=="__main__":run()
