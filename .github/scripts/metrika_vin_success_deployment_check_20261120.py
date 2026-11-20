"""Read-only check of published Tilda VIN-success snippet deployment and goal.
No forms submitted and no source HTML or credentials persisted.
"""
from datetime import datetime,timezone
from pathlib import Path
import json,os,urllib.request,urllib.error,sys
URL="https://leoparts-akpp.ru/mehatronik-dq200"
IDENTIFIER="LEOPARTS_VIN_SUCCESS_5246286101"
COUNTER=113839860
GOAL_ID=686020680
OUT=Path("data/direct_optimization_20261120/vin_success_deployment_status.json")
def utc():return datetime.now(timezone.utc).isoformat()
def save(d):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    p=OUT.with_suffix(".tmp")
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    p.replace(OUT)
def main():
    out={"mode":"read_only","site":URL,"goal_id":GOAL_ID,
         "goal_identifier":IDENTIFIER,"started_utc":utc(),
         "site_write_attempts":0,"metrika_write_attempts":0,"errors":[]}
    save(out)
    try:
        req=urllib.request.Request(URL,method="GET",headers={
           "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"})
        with urllib.request.urlopen(req,timeout=40) as res:
            out["http_status"]=res.status
            data=res.read(1800000).decode("utf-8","replace")
        out["vin_block_present"]=('rec5246286101' in data)
        out["counter_present"]=('113839860' in data)
        out["snippet_marker_present"]=IDENTIFIER in data and 'tildaform:aftersuccess' in data
        out["html_snippet_deployed"]=out["snippet_marker_present"]
        out["status"]="deployed_unverified_conversion" if out["html_snippet_deployed"] else "not_deployed"
        out["interpretation"]="A published script alone is not proof of an actual successful VIN submission or Metrica goal achievement."
    except urllib.error.HTTPError as e:
        out["status"]="site_unavailable";out["errors"].append("HTTP "+str(e.code));e.close()
    except Exception as e:
        out["status"]="site_unavailable";out["errors"].append(type(e).__name__)
    out["finished_utc"]=utc()
    save(out)
    print("LEOPARTS VIN success tracking site status",out["status"],flush=True)
    return 0
if __name__=="__main__":sys.exit(main())
