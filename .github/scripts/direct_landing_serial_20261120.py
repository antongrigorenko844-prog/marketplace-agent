"""Sequential independent read-only landing test after concurrent HTTP failures."""
from datetime import datetime,timezone
import json,time,urllib.request,urllib.error
from pathlib import Path
URL="https://leoparts-akpp.ru/mehatronik-dq200"
OUT=Path("data/direct_audit_20261120/landing_serial_recheck.json")
checks=[]
for n in range(1,5):
    req=urllib.request.Request(URL,headers={"User-Agent":"Mozilla/5.0"},method="GET")
    result={"attempt":n,"start_utc":datetime.now(timezone.utc).isoformat()}
    try:
        with urllib.request.urlopen(req,timeout=25) as resp:
            b=resp.read(600000).decode("utf-8","replace")
            result.update({"status":resp.status,"bytes":len(b),"has_dq200":"dq200" in b.lower(),
              "has_anchor_bez_platy": 'id="bez-platy"' in b or 'href="#bez-platy"' in b,
              "has_anchor_s_platoi": 'id="s-platoi"' in b or 'href="#s-platoi"' in b})
    except urllib.error.HTTPError as exc:
        result["status"]=exc.code;exc.close()
    except Exception as exc:result["error_type"]=type(exc).__name__
    checks.append(result)
    if n<4:time.sleep(3)
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps({"read_only":True,"site":URL,"checks":checks},ensure_ascii=False,indent=2)+"\n")
print("Landing serial recheck",[(r.get("status"),r.get("has_anchor_bez_platy"),r.get("has_anchor_s_platoi")) for r in checks])
