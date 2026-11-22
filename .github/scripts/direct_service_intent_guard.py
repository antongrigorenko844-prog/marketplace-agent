"""LEOPARTS DQ200: campaign-wide service-intent exclusions, preserving product demand.

Owner-approved operation: live audit of ALL 626+ campaign exclusions and ALL
group exclusions, manual keyword overlap check; append ONLY narrow reviewed
service-intent phrases to campaign 716316525. No ads/bid/budget/group writes.
No blind retry following uncertain API response. Quoted exclusions have a fixed
word count (Yandex operator). Explicit service verbs are not broad 'ремонт'.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

CID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
BASE="https://api.direct.yandex.com/json/v501/"
REQ=Path("data/direct_service_intent_request.json")
OUT=Path("data/direct_service_intent_result.json")
# Actual service-only search strings observed in dated paid query reports;
# never block single "ремонт", "мехатроник", "плата" or "запчасти".
ADDITIONS=(
    '"ремонт дсг 7 фольксваген"',
    '"ремонт дсг оренбург"',
    '"ремонт дсг +в калуге адреса"',
    '"ремонт мехатроника dsg 7"',
    '"ремонт мехатроника dq200"',
    '"ремонт dq200 цена"',
    '"ремонт акпп купить"',
    '"замена мехатроника dsg +с гарантией"',
    '"средняя стоимость ремонта снятого мехатроника dq200"',
    '"ремонт dsg7 volkswagen"',
    '"ремонт dsg 7 volkswagen"',
    '"ремонт дсг7 фольксваген"',
    # Unambiguous intention to locate/arrange a service, independent of brand.
    '+где ремонтируют',
    '+где отремонтировать',
    'записаться +на ремонт',
)
# Strict examples that MUST remain outside our new negative coverage.
PROTECTED=(
    "запчасти для ремонта мехатроника dq200",
    "комплект деталей для ремонта мехатроника dq200",
    "купить плату мехатроника dsg 0am927769g",
    "гидроплита dq200 купить",
    "мехатроник dq200 купить",
    "ремкомплект дсг 7",
    "электронная плата мехатроника dq200 купить",
    "втулки для ремонта мехатроника dq200",
    "соленоиды для ремонта dq200",
)
CG={"SelectionCriteria":{"Ids":[CID]},
    "FieldNames":["Id","Name","Type","State","Status","Currency","NegativeKeywords"],
    "UnifiedCampaignFieldNames":["BiddingStrategy","CounterIds","PriorityGoals","AttributionModel"],
    "Page":{"Limit":1000}}
KG={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","Status"],
    "Page":{"Limit":1000}}
GG={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","CampaignId","Name","Status","NegativeKeywords"],
    "Page":{"Limit":1000}}
ALLOWED_PAYLOAD=None
ATTEMPTED=False
def now():return datetime.now(timezone.utc).isoformat()
def scrub(e):
    msg=str(e)
    for k,v in os.environ.items():
        if any(t in k for t in ("TOKEN","SECRET","API_KEY")) and isinstance(v,str) and len(v)>8:
            msg=msg.replace(v,"[redacted]")
    return msg[:340]
def save(report):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    t=OUT.with_suffix(".tmp")
    t.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    t.replace(OUT)
def digest(x):
    return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def api(service,method,params):
    global ATTEMPTED
    if method=="get" and (service,params) in (
        ("campaigns",CG),("keywords",KG),("adgroups",GG)):pass
    elif method=="update" and service=="campaigns" and ALLOWED_PAYLOAD is not None and params==ALLOWED_PAYLOAD and not ATTEMPTED:
        ATTEMPTED=True
    else:
        raise RuntimeError("API access outside safe campaign-only scope")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Direct credentials missing")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8",
             "Accept-Language":"en"}
    login=os.getenv("YANDEX_DIRECT_LOGIN")
    if login:headers["Client-Login"]=login
    req=urllib.request.Request(BASE+service,
        data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode(),
        headers=headers,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=55) as r:
            raw=r.read(3*1024*1024+1)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Direct "+service+" HTTP "+str(code)) from None
    except Exception:
        raise RuntimeError("Direct "+service+" transport error (no automatic retry)") from None
    if len(raw)>3*1024*1024:raise RuntimeError("Excessive API reply")
    d=json.loads(raw)
    if d.get("error") or not isinstance(d.get("result"),dict):
        raise RuntimeError("Direct API "+str((d.get("error") or {}).get("error_code","no result")))
    result=d["result"]
    if "LimitedBy" in result:raise RuntimeError("Incomplete inventory page")
    return result
def inventory():
    cr=api("campaigns","get",CG).get("Campaigns")
    kr=api("keywords","get",KG).get("Keywords")
    gr=api("adgroups","get",GG).get("AdGroups")
    if not isinstance(cr,list) or len(cr)!=1 or not isinstance(kr,list) or not isinstance(gr,list):
        raise RuntimeError("Missing campaign/keywords/groups")
    c=cr[0]
    if (c.get("Id")!=CID or c.get("Name")!=NAME or c.get("Type")!="UNIFIED_CAMPAIGN"
            or c.get("State")!="ON" or c.get("Currency")!="RUB"):
        raise RuntimeError("Campaign identity/state/currency changed")
    if len(kr)!=272 or len(gr)!=6 or any(x.get("CampaignId")!=CID for x in kr+gr):
        raise RuntimeError("Unexpected keyword/group count")
    if len([x for x in kr if x.get("Keyword")!="---autotargeting"])!=266:
        raise RuntimeError("266 manual keywords not present")
    negatives=(c.get("NegativeKeywords") or {}).get("Items")
    if not isinstance(negatives,list) or len(negatives)<626:
        raise RuntimeError("Campaign negative baseline incomplete")
    if len(set(negatives))!=len(negatives) or any(not isinstance(x,str) for x in negatives):
        raise RuntimeError("Bad or duplicate campaign negatives")
    strategy=((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {})
    search=strategy.get("Search") or {}
    limit=((search.get("HighestPosition") or {}).get("WeeklySpendLimit"))
    if (search.get("BiddingStrategyType")!="HIGHEST_POSITION"
        or limit!=4000000000
        or (strategy.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"):
        raise RuntimeError("Budget strategy/routing changed; refuse write")
    return c,kr,gr
def normalize(x):
    return set(re.findall(r"[a-zа-яё0-9]+",str(x).casefold().replace("ё","е")))
def quoted_match(negative,query):
    """Heuristic word-set match for fixed-length exclusions. Not a Yandex matcher."""
    if not (negative[0]=='"' and negative[-1]=='"'):return False
    words=re.findall(r"[a-zа-яё0-9]+",negative[1:-1].casefold().replace("ё","е"))
    q=re.findall(r"[a-zа-яё0-9]+",query.casefold().replace("ё","е"))
    # Safe if exact same word count, multiset; unlike Yandex, does not stem.
    return Counter(words)==Counter(q)
def protect():
    for phrase in ADDITIONS:
        if len(phrase)>150 or not phrase or phrase.strip()!=phrase:
            raise RuntimeError("Invalid reviewed phrase length")
        terms=re.findall(r"[a-zа-яё0-9]+",phrase.casefold())
        if not 2<=len(terms)<=7:
            raise RuntimeError("Too few/many negative words "+phrase)
        if phrase[0]=='"':
            if phrase[-1]!='"' or phrase.count('"')!=2:raise RuntimeError("Invalid quote operator")
        elif phrase not in ('+где ремонтируют','+где отремонтировать','записаться +на ремонт'):
            raise RuntimeError("Unexpected unquoted campaign exclusion")
        if "запчаст" in phrase or "купить плат" in phrase:
            raise RuntimeError("Protected product negative detected")
        for query in PROTECTED:
            if quoted_match(phrase,query):
                raise RuntimeError("Candidate would block protected product query")
            if phrase[0]!='"' and normalize(phrase).issubset(normalize(query)):
                raise RuntimeError("Broad candidate would block protected product query")
def risks(negatives,groups,keywords):
    manual=[x for x in keywords if x.get("Keyword")!="---autotargeting"]
    group_list=[]
    for g in groups:
        neg=((g.get("NegativeKeywords") or {}).get("Items") or [])
        if not isinstance(neg,list):raise RuntimeError("Malformed group negatives")
        group_list.append({"id":g.get("Id"),"name":g.get("Name"),
                           "negative_count":len(neg),"negative_phrases":neg,
                           "possible_parts_buyer_blockers":[x for x in neg
                               if x.strip('"').casefold() in ("запчасти","автозапчасти")]})
    collisions={}
    for candidate in ADDITIONS:
        toks=normalize(candidate)
        hits=[{"id":x.get("Id"),"keyword":x.get("Keyword"),"group_id":x.get("AdGroupId")}
              for x in manual if toks.issubset(normalize(x.get("Keyword","")))]
        if hits:collisions[candidate]=hits
    risky=[x for x in negatives if len(normalize(x))==1 and any(
        t in normalize(x) for t in ("ремонт","запчасти","мехатроник","плата","платы","dq200","dsg"))]
    return {"groups":group_list,"possible_manual_keyword_collisions":collisions,
            "preexisting_one_word_potential_blockers":risky,
            "full_campaign_negative_list":negatives,
            "campaign_negative_count":len(negatives),
            "group_negative_total":sum(x["negative_count"] for x in group_list)}
def nonneg(c):
    return {k:v for k,v in c.items() if k not in ("NegativeKeywords","Status")}
def run(mode):
    global ALLOWED_PAYLOAD
    if mode not in ("plan","apply"):raise ValueError("Invalid mode")
    req=json.loads(REQ.read_text(encoding="utf-8"))
    if (req.get("campaign_id")!=CID or req.get("mode")!=mode
            or req.get("scope")!="service_intent_campaign_level_append_only"
            or req.get("owner_authorized") is not True):
        raise RuntimeError("Owner scope not authorized")
    report={"status":"starting","mode":mode,"campaign_id":CID,"started_utc":now(),
            "write_attempted":False,"mutated_fields":[],"candidate_additions":list(ADDITIONS),
            "protected_product_examples":list(PROTECTED),"errors":[]}
    save(report)
    try:
        protect()
        c,k,g=inventory()
        old=c["NegativeKeywords"]["Items"]
        audit=risks(old,g,k)
        report["audit"]=audit
        missing=[term for term in ADDITIONS if term not in old]
        if audit["possible_manual_keyword_collisions"]:
            raise RuntimeError("Possible keyword overlap: refuse campaign-wide change; inspect report")
        if audit["preexisting_one_word_potential_blockers"]:
            report["warning"]="Some one-word negatives already potentially suppress product demand; not removed automatically."
        if len(missing)!=len(set(missing)):raise RuntimeError("Duplicate additions")
        desired=list(old)+missing
        count_chars=sum(len(x.replace(" ","")) for x in desired)
        if count_chars>19500 or any(len(re.findall(r"[a-zа-яё0-9]+",x))>7 for x in desired):
            raise RuntimeError("Too many negative characters/words for Yandex")
        report.update(original_count=len(old),original_digest=digest(old),
                      missing_additions=missing, proposed_count=len(desired),
                      proposed_char_count=count_chars,group_negative_total=audit["group_negative_total"],
                      manual_keywords=266,autotargeting_conditions=6)
        save(report)
        if mode=="plan":
            report["status"]="plan_safe_no_write";return 0
        if not missing:
            report["status"]="already_applied_verified";return 0
        cc,kk,gg=inventory()
        if cc["NegativeKeywords"]["Items"]!=old or nonneg(cc)!=nonneg(c) or kk!=k or gg!=g:
            raise RuntimeError("Campaign/group/keyword changed before write, abort")
        ALLOWED_PAYLOAD={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":desired}}]}
        report["write_attempted"]=True
        report["write_started_utc"]=now()
        save(report)
        try:
            update=api("campaigns","update",ALLOWED_PAYLOAD)
            results=update.get("UpdateResults") or []
            report["api_accepted"]=len(results)==1 and results[0].get("Id")==CID and not results[0].get("Errors")
            report["api_warnings"]=[x.get("Warnings") for x in results if x.get("Warnings")]
        except Exception as exc:
            report["update_uncertain_error"]=scrub(exc)
        after,ak,ag=inventory()
        observed=after["NegativeKeywords"]["Items"]
        unchanged=Counter(observed)==Counter(desired) and Counter(old)<=Counter(observed)
        campaign_ok=nonneg(c)==nonneg(after)
        groups_ok=Counter(digest(x) for x in g)==Counter(digest(x) for x in ag)
        keywords_ok=k==ak
        report.update(after_count=len(observed),after_digest=digest(observed),
                     original_negatives_preserved=Counter(old)<=Counter(observed),
                     only_expected_negatives_present=unchanged,
                     campaign_other_fields_unchanged=campaign_ok,
                     group_fields_unchanged=groups_ok,
                     keywords_unchanged=keywords_ok,
                     verified_utc=now())
        if unchanged and campaign_ok and groups_ok and keywords_ok:
            report["status"]="applied_verified"
            report["mutated_fields"]=["campaign.NegativeKeywords.Items"]
            return 0
        report["status"]="requires_manual_review"
        return 1
    except Exception as exc:
        report["errors"].append(scrub(exc))
        report["status"]="blocked_or_unverified"
        return 1
    finally:
        report["finished_utc"]=now()
        save(report)
        print(json.dumps({
            "status":report.get("status"),"mode":mode,
            "before":report.get("original_count"),"after":report.get("after_count"),
            "missing":report.get("missing_additions"),
            "manual_collisions":len((report.get("audit") or {}).get("possible_manual_keyword_collisions") or {}),
            "write_attempted":report.get("write_attempted"),
            "groups_unchanged":report.get("group_fields_unchanged"),
            "errors":report.get("errors")
        },ensure_ascii=False),flush=True)
if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in ("plan","apply"):
        raise SystemExit("Usage: python .github/scripts/direct_service_intent_guard.py plan|apply")
    sys.exit(run(sys.argv[1]))
