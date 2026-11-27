"""Full account semantic inventory and disclosed search queries. Read-only."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_preflight_20261120 as api
import direct_performance_deep_20261125 as deep

CID = 716316525
RID = os.environ.get("GITHUB_RUN_ID", "local")
if not re.fullmatch(r"[0-9]+|local", RID):
    raise ValueError("Invalid run ID")
OUTDIR = Path("data/direct_semantics_20261127") / ("live_" + RID)
OUT = OUTDIR / "inventory.json"
api.ALLOWED = ("campaigns", "adgroups", "keywords", "ads", "feeds",
               "sitelinks", "negativekeywordsharedsets", "reports")
J = {"mode": "strict_read_only", "external_mutations": 0,
     "started_utc": datetime.now(timezone.utc).isoformat(),
     "report_timezone": "Europe/Moscow", "sections": {}, "errors": {}}

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")

def section(name, call):
    try:
        value = call()
        J["sections"][name] = {"status": "ok", "data": value}
        return value
    except Exception as exc:
        msg = api.scrub(exc)
        J["sections"][name] = {"status": "error", "error": msg}
        J["errors"][name] = msg
    finally:
        write(OUT, J)

def batched_get(service, ids, params, key):
    rows = []
    for start in range(0, len(ids), 10):
        selection = {"CampaignIds": ids[start:start+10]}
        rows.extend(api.get(service, params | {"SelectionCriteria": selection,
                                               "Page": {"Limit": 1000}}, key))
    if len({r["Id"] for r in rows}) != len(rows):
        raise ValueError("Repeated " + service + " ID")
    if any(r.get("CampaignId") not in ids for r in rows):
        raise ValueError("Unexpected campaign in " + service)
    return rows

campaign_params = {
    "SelectionCriteria": {},
    "FieldNames": ["Id", "Name", "Type", "State", "Status", "StatusPayment",
                   "StartDate", "NegativeKeywords", "TimeZone"],
    "UnifiedCampaignFieldNames": ["BiddingStrategy", "CounterIds", "PriorityGoals",
                                  "AttributionModel", "NegativeKeywordSharedSetIds",
                                  "TrackingParams", "Settings"],
    "TextCampaignFieldNames": ["BiddingStrategy", "CounterIds", "PriorityGoals",
                               "AttributionModel", "NegativeKeywordSharedSetIds"],
    "Page": {"Limit": 1000}}
campaigns = section("campaigns", lambda: api.get("campaigns", campaign_params, "Campaigns"))
if not campaigns or CID not in {c["Id"] for c in campaigns}:
    J["status"] = "blocked_no_verified_account"
    write(OUT, J)
    raise SystemExit(1)
ids = sorted({c["Id"] for c in campaigns if c.get("State") == "ON"} | {CID})
J["detailed_campaign_ids"] = ids
J["other_campaigns_inventory_only"] = [c["Id"] for c in campaigns if c["Id"] not in ids]
groups = section("groups", lambda: batched_get("adgroups", ids, {
    "FieldNames": ["Id", "Name", "CampaignId", "Status", "Type", "Subtype", "RegionIds",
                   "RestrictedRegionIds", "NegativeKeywords", "NegativeKeywordSharedSetIds", "TrackingParams"],
    "UnifiedAdGroupFieldNames": ["OfferRetargeting"]}, "AdGroups"))
keywords = section("keywords", lambda: batched_get("keywords", ids, {
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status",
                   "ServingStatus", "Bid", "ContextBid", "StrategyPriority",
                   "AutotargetingSearchBidIsAuto", "UserParam1", "UserParam2"],
    "AutotargetingSettingsCategoriesFieldNames": ["Exact", "Narrow", "Alternative", "Accessory", "Broader"],
    "AutotargetingSettingsBrandOptionsFieldNames": ["WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand"]},
    "Keywords"))
shared = section("negative_shared_sets", lambda: api.get("negativekeywordsharedsets", {
    "FieldNames": ["Id", "Name", "NegativeKeywords", "Associated"], "Page": {"Limit": 1000}},
    "NegativeKeywordSharedSets"))
ads = section("ads", lambda: batched_get("ads", ids, {
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
    "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId"],
    "ShoppingAdFieldNames": ["FeedId", "SitelinkSetId", "FeedFilterConditions"],
    "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "SitelinkSetId"]}, "Ads"))
feeds = section("feeds", lambda: api.get("feeds", {"SelectionCriteria": {},
    "FieldNames": ["Id", "Name", "SourceType", "Status", "UpdatedAt", "NumberOfItems", "TitleAndTextSources"],
    "UrlFeedFieldNames": ["Url"], "Page": {"Limit": 1000}}, "Feeds"))
sitelink_ids = sorted({v["SitelinkSetId"] for ad in ads or [] for k, v in ad.items()
                      if isinstance(v, dict) and v.get("SitelinkSetId")})
if sitelink_ids:
    section("sitelinks", lambda: api.get("sitelinks", {"SelectionCriteria": {"Ids": sitelink_ids},
        "FieldNames": ["Id", "Sitelinks"], "Page": {"Limit": 1000}}, "SitelinksSets"))
else:
    section("sitelinks", lambda: [])

def full_queries(campaign_id):
    deep.CID = campaign_id
    rows, chunks, errors = [], [], []
    start = deep.START
    fields = ["Date", "CampaignId", "AdGroupId", "AdId", "Query", "CriterionType",
              "CriterionId", "Criterion", "MatchedKeyword", "TargetingCategory", "MatchType",
              "Impressions", "Clicks", "Cost", "Conversions"]
    while start <= deep.TODAY:
        end = min(start + timedelta(days=6), deep.TODAY)
        try:
            batch = deep.report(start, end, "SEARCH_QUERY_PERFORMANCE_REPORT", fields,
                                "all-semantic-" + str(campaign_id) + "-" + start.isoformat())
            rows.extend(batch)
            chunks.append({"from": start.isoformat(), "to": end.isoformat(), "rows": len(batch)})
        except Exception as exc:
            errors.append({"from": start.isoformat(), "to": end.isoformat(), "error": api.scrub(exc)})
        start = end + timedelta(days=1)
    result = {"campaign_id": campaign_id, "full_days": [deep.START.isoformat(), deep.END.isoformat()],
              "today": deep.TODAY.isoformat(), "includes_zero_click_queries": True,
              "coverage_complete": not errors, "chunks": chunks, "errors": errors, "rows": rows}
    filename = "queries_" + str(campaign_id) + ".json"
    write(OUTDIR / filename, result)
    if errors:
        J["errors"]["query_chunks_" + str(campaign_id)] = errors
    return {"file": filename, "rows": len(rows), "coverage_complete": not errors, "chunks": chunks}

for campaign_id in ids:
    section("all_queries_" + str(campaign_id), lambda c=campaign_id: full_queries(c))
deep.CID = CID
section("campaigns_after", lambda: api.get("campaigns", campaign_params, "Campaigns"))
J["campaign_configuration_unchanged_during_read"] = (
    J["sections"]["campaigns"]["data"] == J["sections"].get("campaigns_after", {}).get("data"))
J["finished_utc"] = datetime.now(timezone.utc).isoformat()
J["status"] = "partial" if J["errors"] else "complete"
write(OUT, J)
print(json.dumps({"status": J["status"], "path": str(OUT), "campaign_ids": ids, "errors": J["errors"]},
                 ensure_ascii=False))

