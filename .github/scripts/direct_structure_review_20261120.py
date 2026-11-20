"""Read-only Direct targeting and feed segmentation for campaign 716316525."""
import json,os,sys,time,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime,timezone
CID=716316525
OUT=Path("data/direct_optimization_20261120/structure_review.json")
BASE="https://api.direct.yandex.com/json/v501/"
def now():return datetime.now(timezone.utc).isoformat()
def read(svc,params):
    if svc not in ("ads","keywords","campaigns"):raise ValueError("Unsupported service")
    if params.get("SelectionCriteria") not in ({"CampaignIds":[CID]},{"Ids":[CID]}):
        raise ValueError("Out-of-scope selection")
    h={"Authorization":"Bearer "+os.environ["YANDEX_DIRECT_TOKEN"],
       "Accept-Language":"en","Content-Type":"application/json; charset=utf-8"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(BASE+svc,data=json.dumps({"method":"get","params":params}).encode(),headers=h,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=65) as resp:d=json.load(resp)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError(svc+" HTTP "+str(code))
    if d.get("error"):raise RuntimeError(svc+" API error "+str(d["error"].get("error_code")))
    result=d.get("result") or {}
    if result.get("LimitedBy"):raise RuntimeError("Unexpected pagination")
    return result
def main():
    res={"status":"in_progress","scope":"read_only","captured_start_utc":now(),"campaign_id":CID,
         "errors":[],"mutations":0}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    def save():OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save()
    try:
        c=read("campaigns",{"SelectionCriteria":{"Ids":[CID]},
           "FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
           "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}})
        camp=c.get("Campaigns") or []
        if len(camp)!=1 or camp[0].get("Id")!=CID:raise RuntimeError("Campaign mismatch")
        k=read("keywords",{"SelectionCriteria":{"CampaignIds":[CID]},
           "FieldNames":["Id","Keyword","CampaignId","AdGroupId","State","Status","AutotargetingCategories"],
           "AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
           "AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
           "Page":{"Limit":1000}})
        keywords=k.get("Keywords") or []
        if len(keywords)!=272 or any(x.get("CampaignId")!=CID for x in keywords):
            raise RuntimeError("Keyword inventory changed")
        autotarget=[{"Id":x["Id"],"AdGroupId":x["AdGroupId"],"Keyword":x["Keyword"],"State":x.get("State"),
            "AutotargetingSettings":x.get("AutotargetingSettings"),
            "AutotargetingCategories":x.get("AutotargetingCategories")} for x in keywords
            if x.get("Keyword")=="---autotargeting"]
        ads=read("ads",{"SelectionCriteria":{"CampaignIds":[CID]},
          "FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status"],
          "ResponsiveAdFieldNames":["Titles","Texts","Href"],
          "ShoppingAdFieldNames":["FeedId","FeedProcessingStatus","FeedFilterConditions","GenerationScopes",
            "ListingFeedFilterConditions","TitleSources","TextSources","ListingTitleSources","ListingTextSources"],
          "Page":{"Limit":1000}})
        ad_items=ads.get("Ads") or []
        if len(ad_items)!=8 or any(x.get("CampaignId")!=CID for x in ad_items):
            raise RuntimeError("Ads inventory mismatch")
        res.update(status="complete",captured_utc=now(),
          campaign={"Id":camp[0]["Id"],"Name":camp[0]["Name"],"State":camp[0]["State"],
                    "BiddingStrategy":camp[0].get("UnifiedCampaign",{}).get("BiddingStrategy"),
                    "NegativeKeywordCount":len((camp[0].get("NegativeKeywords") or {}).get("Items") or [])},
          autotargeting=autotarget,ads=ad_items,
          warnings=["FeedFilterConditions and GenerationScopes are data, not proof of effective offer-level delivery.",
            "Current keyword settings can take up to a day to affect impressions after modification."])
    except Exception as e:
        res["status"]="error";res["errors"].append(str(e)[:250])
    finally:
        res["finished_utc"]=now();save()
        print("Structure review",res["status"],"autotargets",len(res.get("autotargeting",[])),
              "ads",len(res.get("ads",[])),"errors",res["errors"],flush=True)
    return 0 if res["status"]=="complete" else 1
if __name__=="__main__":sys.exit(main())
