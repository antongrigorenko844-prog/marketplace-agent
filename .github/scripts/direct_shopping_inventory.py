"""Read-only inventory before approved DQ200 advertising changes."""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

CID = 716316525
OUT = Path("data/direct_live_shopping_inventory.json")
TOKEN = os.environ["YANDEX_DIRECT_TOKEN"]
HEAD = {"Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Language": "ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):
    HEAD["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]

SHOPPING_FIELDS = [
    "SitelinkSetId", "SitelinksModeration", "AdExtensions", "BusinessId",
    "FeedId", "GenerationScopes", "FeedFilterConditions", "FeedProcessingStatus",
    "TitleSources", "TextSources", "DefaultTexts", "ListingFeedFilterConditions",
    "ListingTitleSources", "ListingTextSources",
]
report = {"campaign_id": CID, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
          "workflow_commit": os.environ.get("GITHUB_SHA"),
          "mode": "read_only", "errors": []}

def safe_error(value):
    text = str(value).replace(TOKEN, "[redacted]")
    return text[:1500]

def get(service, params, version="v501"):
    req = urllib.request.Request(
        "https://api.direct.yandex.com/json/" + version + "/" + service,
        data=json.dumps({"method": "get", "params": params}, ensure_ascii=False).encode("utf-8"),
        headers=HEAD,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read()
            content_type = res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{service}.get HTTP {error.code}: {safe_error(body)}") from error
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(f"{service}.get returned non-JSON data; type={content_type}, bytes={len(raw)}") from error
    if body.get("error"):
        raise RuntimeError(f"{service}.get: {safe_error(json.dumps(body['error'], ensure_ascii=False))}")
    if "result" not in body:
        raise RuntimeError(f"{service}.get missing result")
    return body["result"]

def pages(service, params, collection, version="v501"):
    rows = []
    offset = 0
    for _ in range(100):
        result = get(service, {**params, "Page": {"Limit": 1000, "Offset": offset}}, version)
        chunk = result.get(collection, [])
        rows.extend(chunk)
        if "LimitedBy" in result:
            next_offset = result["LimitedBy"]
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError("Invalid pagination")
            offset = next_offset
        elif len(chunk) < 1000:
            return rows
        else:
            offset += len(chunk)
    raise RuntimeError("Pagination limit exceeded")

def collect(label, fn):
    try:
        report[label] = fn()
    except Exception as error:
        report["errors"].append({"section": label, "error": safe_error(error)})

class LandingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = set()
        self.title = []
        self.in_title = False
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for key in ("id", "name"):
            if values.get(key):
                self.anchors.add(values[key])
        if tag == "title":
            self.in_title = True
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)

def landing():
    url = "https://leoparts-akpp.ru/mehatronik-dq200"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as response:
        raw = response.read(8 * 1024 * 1024)
        result = {"url": response.geturl(), "http_status": response.status}
    parser = LandingParser()
    parser.feed(raw.decode("utf-8", "replace"))
    result.update({
        "title": "".join(parser.title).strip(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "anchors": {anchor: anchor in parser.anchors for anchor in ("bez-platy", "s-platoi", "platy")},
    })
    return result

collect("ads", lambda: pages("ads", {
    "SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "Status", "State", "StatusClarification"],
    "ShoppingAdFieldNames": SHOPPING_FIELDS,
}, "Ads"))
collect("groups", lambda: pages("adgroups", {
    "SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "CampaignId", "Name", "Status", "RegionIds", "NegativeKeywords"],
}, "AdGroups", "v5"))
collect("keywords", lambda: pages("keywords", {
    "SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status"],
}, "Keywords", "v5"))
feed_ids = sorted({ad["ShoppingAd"]["FeedId"] for ad in report.get("ads", [])
                   if ad.get("ShoppingAd", {}).get("FeedId") is not None})
if feed_ids:
    collect("feeds", lambda: get("feeds", {
        "SelectionCriteria": {"Ids": feed_ids},
        "FieldNames": ["Id", "Name", "SourceType", "Status", "UpdatedAt",
                       "NumberOfItems", "TitleAndTextSources"],
    })["Feeds"])
sitelink_ids = sorted({ad["ShoppingAd"]["SitelinkSetId"] for ad in report.get("ads", [])
                       if ad.get("ShoppingAd", {}).get("SitelinkSetId") is not None})
if sitelink_ids:
    collect("sitelink_sets", lambda: get("sitelinks", {
        "SelectionCriteria": {"Ids": sitelink_ids},
        "FieldNames": ["Id", "Sitelinks"],
    })["SitelinksSets"])
collect("landing", landing)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("Read-only inventory:", len(report.get("ads", [])), "ads;",
      len(report.get("groups", [])), "groups;",
      len(report.get("keywords", [])), "keywords;",
      len(report["errors"]), "errors")
for error in report["errors"]:
    print(error["section"] + ": " + error["error"])
if "ads" not in report or "groups" not in report:
    sys.exit(1)
