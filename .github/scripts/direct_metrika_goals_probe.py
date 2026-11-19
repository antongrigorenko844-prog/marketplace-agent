"""One GET-only probe of two known goals using the existing Direct token.

No retries, redirects, form submissions, credential output, or API mutations.
Only the two selected goal definitions are persisted, never the full response.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

COUNTER_ID = 113839860
GOAL_IDS = {675506966, 675507028}
URL = "https://api-metrika.yandex.net/management/v1/counter/113839860/goals"
OUT = Path("data/direct_metrika_goals_probe_20261119.json")


def sanitized(value):
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(r"(?i)\b(?:Bearer|OAuth)\s+\S+", "[redacted authorization]", text)
    text = re.sub(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)(\s*[:=]\s*)[^&\s]+",
                  r"\1\2[redacted]", text)
    if text.startswith(("http://", "https://")):
        try:
            parts = urllib.parse.urlsplit(text)
            # URL-goal conditions remain readable; embedded credentials do not.
            host = parts.hostname or ""
            if parts.port:
                host += ":" + str(parts.port)
            text = urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
        except ValueError:
            return "[invalid URL condition]"
    return text[:2000]


def selected_goals(body):
    goals = body.get("goals")
    if not isinstance(goals, list):
        raise ValueError("Invalid response schema")
    selected = []
    for goal in goals:
        if not isinstance(goal, dict) or str(goal.get("id")) not in {str(i) for i in GOAL_IDS}:
            continue
        item = {"id": int(goal["id"]), "name": sanitized(goal.get("name")),
                "type": sanitized(goal.get("type"))}
        conditions = goal.get("conditions")
        if isinstance(conditions, list):
            item["conditions"] = [{key: sanitized(condition[key]) for key in ("type", "url")
                                    if key in condition} if isinstance(condition, dict) else None
                                   for condition in conditions[:50]]
            item["conditions_truncated"] = len(conditions) > 50
        else:
            item["conditions"] = None
        selected.append(item)
    return selected


def run():
    report = {"mode": "read_only", "status": "in_progress", "counter_id": COUNTER_ID,
              "goal_ids": sorted(GOAL_IDS), "request_method": "GET", "endpoint": URL,
              "attempts": 0, "timeout_seconds": 30, "http_status": None,
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "workflow_commit": os.environ.get("GITHUB_SHA"), "goals": []}
    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        report["status"] = "credential_unavailable"
    else:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        # Metrica's documented scheme is OAuth; the existing token is unchanged.
        req = urllib.request.Request(URL, method="GET", headers={
            "Authorization": "OAuth " + token, "Accept": "application/json"})
        report["attempts"] = 1
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                report["http_status"] = response.status
                raw = response.read(1024 * 1024 + 1)
            if report["http_status"] != 200:
                report["status"] = "unexpected_http_status"
            elif len(raw) > 1024 * 1024:
                report["status"] = "response_size_limit"
            else:
                report["goals"] = selected_goals(json.loads(raw))
                report["missing_goal_ids"] = sorted(GOAL_IDS - {g["id"] for g in report["goals"]})
                report["status"] = "partial" if report["missing_goal_ids"] else "complete"
        except urllib.error.HTTPError as error:
            report["http_status"] = error.code
            report["status"] = "limited" if error.code in (401, 403) else "http_error"
            error.close()  # Never read or persist an error body or headers.
        except (ValueError, TypeError, UnicodeError):
            report["status"] = "invalid_response"
        except Exception:
            report["status"] = "transport_error"  # No exception string/credential leakage.
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUT)
    print("Read-only Metrica goals probe:", report["status"], "; HTTP:", report["http_status"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
