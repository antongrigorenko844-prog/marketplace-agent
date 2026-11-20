"""Final independent read-only verification of both Nov 20 Direct improvements."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import json,sys
import direct_structure_review_20261120 as live

CID=716316525
OUT=Path("data/direct_optimization_20261120/final_state.json")
BASE=Path("data/direct_optimization_20261120/structure_review.json")
TARGET=205826183946
FOUR=['"02e мехатроник в сборе"','"ремонт дсг вологда"',
      '"ремонт дсг"','"признаки износа сцепления дсг 7 dq200"']
def now():return datetime.now(timezone.utc).isoformat()
def read():
    campaign=live.read("campaigns",{"SelectionCriteria":{"Ids":[CID]},
        "FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
        "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}).get("Campaigns") or []
    keywords=live.read("keywords",{"SelectionCriteria":{"CampaignIds":[CID]},
        "FieldNames":["Id","Keyword","CampaignId","AdGroupId","State","Status","AutotargetingCategories"],
        "AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
        "AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
        "Page":{"Limit":1000}}).get("Keywords") or []
    ads=live.read("ads",{"SelectionCriteria":{"CampaignIds":[CID]},
        "FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status"],
        "ResponsiveAdFieldNames":["Titles","Texts","Href"],
        "ShoppingAdFieldNames":["FeedId","FeedProcessingStatus","FeedFilterConditions","GenerationScopes",
             "ListingFeedFilterConditions","TitleSources","TextSources","ListingTitleSources","ListingTextSources"],
        "Page":{"Limit":1000}}).get("Ads") or []
    return campaign,keywords,ads
def main():
    report={"status":"in_progress","source":"live Direct GET only","campaign_id":CID,
            "captured_utc":now(),"external_mutations":0,"errors":[]}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    def save():OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save()
    try:
        base=json.loads(BASE.read_text(encoding="utf-8"))
        c,k,ads=read()
        if len(c)!=1 or c[0].get("Id")!=CID or c[0].get("State")!="ON":
            raise RuntimeError("Campaign no longer matches")
        neg=(c[0].get("NegativeKeywords") or {}).get("Items") or []
        b=(c[0].get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
        if (len(neg)!=134 or len(set(neg))!=134 or
           not all(neg.count(x)==1 for x in FOUR) or
           b.get("Search",{}).get("BiddingStrategyType")!="HIGHEST_POSITION" or
           b.get("Network",{}).get("BiddingStrategyType")!="SERVING_OFF" or
           (b.get("Search",{}).get("HighestPosition") or {}).get("WeeklySpendLimit") is not None):
            raise RuntimeError("Campaign expected negative and budget settings differ")
        manual=[x for x in k if x.get("Keyword")!="---autotargeting"]
        autos=[x for x in k if x.get("Keyword")=="---autotargeting"]
        if len(k)!=272 or len(manual)!=266 or len(autos)!=6:
            raise RuntimeError("Keyword inventory changed")
        base_auto={x["Id"]:x for x in base["autotargeting"]}
        for x in autos:
            old=base_auto[x["Id"]]
            expected=deepcopy(old)
            if x["Id"]==TARGET:
                expected["AutotargetingSettings"]["Categories"]["Accessory"]="NO"
                for y in (expected.get("AutotargetingCategories") or {}).get("Items",[]):
                    if y.get("Category")=="ACCESSORY":y["Value"]="NO"
            actual={k:x.get(k) for k in ["Id","AdGroupId","Keyword","State","AutotargetingSettings","AutotargetingCategories"]}
            if actual!=expected:
                raise RuntimeError("Changed targeting outside one approved category: "+str(x["Id"]))
        if ads!=base["ads"]:raise RuntimeError("Ads changed unexpectedly from verified baseline")
        report.update(status="verified",captured_utc=now(),negative_count=len(neg),
            four_exact_phrases_present=FOUR,weekly_limit_removed=True,
            all_266_manual_keywords_preserved=True,all_eight_ads_preserved=True,
            all_other_targeting_unchanged=True,
            with_board_accessory_disabled=True,
            with_board_autotargeting_criterion_id=TARGET,finished_utc=now())
    except Exception as e:
        report["status"]="needs_attention";report["errors"].append(str(e)[:260])
    finally:
        report["finished_utc"]=now();save()
        print("Direct final verification",report["status"],"negatives",report.get("negative_count"),flush=True)
    return 0 if report["status"]=="verified" else 1
if __name__=="__main__":sys.exit(main())
