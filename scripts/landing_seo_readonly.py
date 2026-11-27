"""Anonymous GET-only audit of known LEOPARTS commercial URLs.

Run from an existing marketplace-agent checkout with requests/beautifulsoup4.
No API writes, credentials, login, form submission, browser, or site mutations.
Only --output is written locally. Redirects outside the owned host stop the probe.
"""
import argparse
import hashlib
import json
import re
import urllib.parse as U
import urllib.robotparser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://leoparts-akpp.ru"
HOSTS = {"leoparts-akpp.ru", "www.leoparts-akpp.ru"}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"}
SKUS = {"0am325025H", "0am325025HX", "0AM927769D", "0AM927769E", "0AM927769F", "0AM927769G", "0AM927769K", "0am325025", "0AM325587E", "0am325025m", "0am325025z", "0am325091f-set", "0AM927769N", "0am325066ae", "0AM325443D", "0am325091g"}
CLAIMS = re.compile(r"клонир\w*|прошив\w*|адаптац\w*|установ\w*|ремонт\w*|контракт\w*|коробк\w*|готов\w*|программ\w*|0AM927769[A-Z]?", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def url_clean(url):
    return U.urldefrag(str(url))[0]


def owned(url):
    p = U.urlsplit(url)
    return p.scheme in {"http", "https"} and p.hostname in HOSTS and not p.username and not p.password


def get_owned(url):
    """Read response bytes without cookies/auth, following only owned hosts."""
    chain = []
    current = url_clean(url)
    with requests.Session() as session:
        for _ in range(9):
            if not owned(current):
                return None, chain, "outside_owned_domain"
            response = session.get(current, headers=HEADERS, timeout=(8, 20), allow_redirects=False)
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                target = U.urljoin(current, response.headers["Location"])
                chain.append({"url": current, "http": response.status_code, "location": target})
                current = target
                continue
            return response, chain, None
    return None, chain, "redirect_limit"


def parse_page(response, chain, references):
    soup = BeautifulSoup(response.content, "html.parser")
    canonical = [U.urljoin(response.url, n.get("href", "")) for n in soup.find_all("link") if "canonical" in (n.get("rel") or [])]
    meta = [{"name": (n.get("name") or n.get("http-equiv") or "").lower(), "content": n.get("content", "")} for n in soup.find_all("meta")]
    meta_robots = [n for n in meta if n["name"] in {"robots", "googlebot", "yandex", "yandexbot"}]
    anchors = {str(n.get("id")) for n in soup.find_all(id=True)} | {str(n.get("name")) for n in soup.find_all("a", attrs={"name": True})}
    anchor_checks = [{"href": x["url"], "fragment": U.unquote(U.urlsplit(x["url"]).fragment), "exists": U.unquote(U.urlsplit(x["url"]).fragment) in anchors} for x in references if U.urlsplit(x["url"]).fragment]
    allrecords = soup.find(id="allrecords")
    schema = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            schema.append(json.loads(node.get_text()))
        except (ValueError, TypeError):
            schema.append({"parse_error": True, "text_preview": node.get_text()[:500]})
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    lines = [re.sub(r"\s+", " ", x).strip() for x in soup.get_text("\n", strip=True).splitlines()]
    lines = [x for x in lines if x]
    text = "\n".join(lines)
    claims = [x for x in lines if CLAIMS.search(x)]
    return {
        "http": response.status_code, "final_url": response.url, "redirects": chain,
        "response_headers": {k: response.headers[k] for k in ("Content-Type", "X-Robots-Tag", "WWW-Authenticate", "Server", "Cache-Control", "Last-Modified") if k in response.headers},
        "canonical": canonical, "meta_robots": meta_robots,
        "meta_description": [x["content"] for x in meta if x["name"] == "description"],
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "h1": [x.get_text(" ", strip=True) for x in soup.find_all("h1")],
        "h2": [x.get_text(" ", strip=True) for x in soup.find_all("h2")],
        "anchor_checks": anchor_checks,
        "page_id": allrecords.get("data-tilda-page-id") if allrecords else None,
        "project_id": allrecords.get("data-tilda-project-id") if allrecords else None,
        "password_inputs": len(soup.select("input[type=password]")),
        "authentication_detected": bool(response.status_code == 401 or response.headers.get("WWW-Authenticate") or soup.select("input[type=password]")),
        "content_claims": claims, "visible_text": text[:70000], "visible_text_truncated": len(text) > 70000,
        "json_ld": schema, "html_bytes": len(response.content), "html_sha256": hashlib.sha256(response.content).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/direct_semantics_20261127/landing_seo_readonly.json")
    parser.add_argument("--snapshot", help="Optional fresh Direct read-only snapshot JSON; otherwise newest known live_30d.json")
    args = parser.parse_args()
    report = {"started_utc": now(), "mode": "READ_ONLY_ANONYMOUS_GET", "external_mutations": 0, "scope": "HTTP from existing runtime; no real Googlebot IP, no browser JS execution, no private GSC access", "sources": [], "errors": []}
    references = []
    def add(url, source, **extra):
        if owned(url):
            references.append({"url": url, "source": source, **extra})
    for path in ("/", "/catalog", "/mehatronik-dq200", "/remontdsg7dq200"):
        add(BASE + path, "core_commercial_or_known_repair_slug")
    # Actual historical URLs returned by public search, not inferred GSC examples.
    for path in ("/catalog/tproduct/976046353374-usilennaya-gidroplita-dsg7-dq200-0am3250", "/tproduct/131718968624-remkomplekt-mehatronika-dsg-7-dq200-plit"):
        add(BASE + path, "historical_public_search_result_not_GSC")
    feed_path = Path("feeds/dq200_candidate.yml")
    feed_offers = []
    if feed_path.is_file():
        body = feed_path.read_bytes()
        root = ET.fromstring(body)
        report["sources"].append({"path": str(feed_path), "sha256": hashlib.sha256(body).hexdigest(), "catalog_date_attribute": root.get("date")})
        for offer in root.findall(".//offer"):
            entry = {"id": offer.get("id"), "available": offer.get("available"), **{n.tag: n.text for n in offer if n.tag != "picture"}, "params": [{"name": x.get("name"), "value": x.text} for x in offer.findall("param")]}
            feed_offers.append(entry)
            add(entry.get("url", ""), "feed_4061297", offer_id=entry["id"], name=entry.get("name"))
    report["feed_offers"] = feed_offers
    candidates = [Path(args.snapshot)] if args.snapshot else sorted(Path("data/direct_traffic_quality").glob("*/live_30d.json"), reverse=True)
    if candidates and candidates[0].is_file():
        path = candidates[0]
        snapshot = json.loads(path.read_text())
        report["sources"].append({"path": str(path), "started_utc": snapshot.get("started_utc"), "finished_utc": snapshot.get("finished_utc")})
        ad_data = snapshot.get("sections", {}).get("feed_ads", {}).get("data", {})
        report["advertising"] = ad_data
        for ad in ad_data.get("ads", []):
            for key in ("ResponsiveAd", "TextAd", "DynamicTextAd"):
                data = ad.get(key) or {}
                add(data.get("Href", ""), "direct_ad", ad_id=ad.get("Id"), group_id=ad.get("AdGroupId"), state=ad.get("State"))
        for linkset in ad_data.get("sitelink_sets", []):
            for link in linkset.get("Sitelinks", []):
                add(link.get("Href", ""), "direct_sitelink", set_id=linkset.get("Id"), title=link.get("Title"))
    initial = {}
    parts = set()
    recids = set()
    for path in ("/catalog", "/"):
        try:
            response, chain, error = get_owned(BASE + path)
            if response is not None:
                initial[BASE + path] = (response, chain)
                parts |= set(re.findall(r'storepart(?:uid|_uid|Uid)?["\']?\s*[:=]\s*["\']?(\d{6,})', response.text, re.I))
                parts |= set(re.findall(r'"storepart"\s*:\s*"(\d+)"', response.text))
                recids |= set(re.findall(r'id="rec(\d+)"', response.text))
            elif error:
                report["errors"].append({"stage": "catalog_discovery", "url": BASE + path, "error": error})
        except requests.RequestException as exc:
            report["errors"].append({"stage": "catalog_discovery", "url": BASE + path, "error": type(exc).__name__})
    products = []
    for part in sorted(parts):
        slice_no = 1
        seen_slices = set()
        while slice_no not in seen_slices and len(seen_slices) < 20:
            seen_slices.add(slice_no)
            try:
                response = requests.get("https://store.tildaapi.com/api/getproductslist/", headers=HEADERS, timeout=(8, 20), params={"storepartuid": part, "recid": next(iter(sorted(recids)), ""), "c": "", "getparts": "true", "getoptions": "true", "slice": slice_no, "size": 100})
                response.raise_for_status()
                data = response.json()
                batch = data.get("products") or []
                products.extend({**p, "audited_storepart": part} for p in batch)
                if not batch or not data.get("nextslice"):
                    break
                slice_no = int(data["nextslice"])
            except (requests.RequestException, ValueError, TypeError) as exc:
                report["errors"].append({"stage": "public_store", "storepart": part, "slice": slice_no, "error": type(exc).__name__})
                break
    selected_products = []
    feed_ids = {x["id"] for x in feed_offers}
    feed_uids = {m.group(1) for x in feed_offers for m in [re.search(r"/tproduct/(\d+)", x.get("url", ""))] if m}
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if sku in SKUS or sku in feed_ids or str(product.get("uid")) in feed_uids or re.fullmatch(r"0AM927769[DEFGK]", sku, re.I):
            selected_products.append({key: product.get(key) for key in ("uid", "sku", "title", "name", "descr", "text", "url", "price", "priceold", "quantity", "editions", "externalid", "audited_storepart")})
            add(product.get("url", ""), "fresh_public_catalog", sku=sku, uid=product.get("uid"))
    report["public_catalog"] = {"storeparts": sorted(parts), "products_observed": len(products), "selected": selected_products}
    robots_response = None
    robots = None
    sitemap_queue = [BASE + "/sitemap.xml", BASE + "/sitemap-store.xml"]
    try:
        robots_response, chain, error = get_owned(BASE + "/robots.txt")
        if robots_response is not None:
            report["robots_txt"] = {"http": robots_response.status_code, "url": robots_response.url, "redirects": chain, "text": robots_response.text}
            if robots_response.status_code == 200:
                robots = urllib.robotparser.RobotFileParser()
                robots.parse(robots_response.text.splitlines())
                sitemap_queue += robots.site_maps() or []
        else:
            report["robots_txt"] = {"error": error, "redirects": chain}
    except requests.RequestException as exc:
        report["robots_txt"] = {"error": type(exc).__name__}
    sitemap_urls = set()
    sitemap_reports = []
    visited = set()
    while sitemap_queue and len(visited) < 15:
        url = sitemap_queue.pop(0)
        if url in visited or not owned(url):
            continue
        visited.add(url)
        try:
            response, chain, error = get_owned(url)
            entry = {"url": url, "redirects": chain}
            if response is None:
                entry["error"] = error
            else:
                entry["http"] = response.status_code
                entry["final_url"] = response.url
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    locs = [x.text.strip() for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "loc" and x.text]
                    entry["xml_type"] = root.tag.rsplit("}", 1)[-1]
                    entry["loc_count"] = len(locs)
                    if entry["xml_type"] == "sitemapindex":
                        sitemap_queue += locs
                    else:
                        sitemap_urls.update(locs)
            sitemap_reports.append(entry)
        except (requests.RequestException, ET.ParseError) as exc:
            sitemap_reports.append({"url": url, "error": type(exc).__name__})
    report["sitemaps"] = {"files": sitemap_reports, "url_count": len(sitemap_urls), "urls": sorted(sitemap_urls)}
    target_map = {}
    for ref in references:
        target_map.setdefault(url_clean(ref["url"]), []).append(ref)
    def probe(item):
        url, refs = item
        row = {"url": url, "references": refs, "checked_utc": now(), "in_sitemap": url in sitemap_urls}
        if robots:
            row["robots_txt_allowed"] = {agent: robots.can_fetch(agent, url) for agent in ("Googlebot", "YandexBot", "*")}
        try:
            if url in initial:
                response, chain = initial[url]
                error = None
            else:
                response, chain, error = get_owned(url)
            if response is None:
                row.update(error=error, redirects=chain)
            else:
                row.update(parse_page(response, chain, refs))
                row["canonical_in_sitemap"] = {x: x in sitemap_urls for x in row["canonical"]}
        except requests.RequestException as exc:
            row["error"] = type(exc).__name__
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        report["checks"] = list(pool.map(probe, target_map.items()))
    report["finished_utc"] = now()
    report["summary"] = {"unique_urls_checked": len(report["checks"]), "http_status_counts": {str(code): sum(x.get("http") == code for x in report["checks"]) for code in sorted({x["http"] for x in report["checks"] if "http" in x})}, "request_errors": sum(bool(x.get("error")) for x in report["checks"]), "missing_anchor_targets": sum(not a["exists"] for x in report["checks"] for a in x.get("anchor_checks", []))}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "finished_utc": report["finished_utc"], "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
