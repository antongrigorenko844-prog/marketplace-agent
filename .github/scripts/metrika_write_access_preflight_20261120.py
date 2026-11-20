"""LEOPARTS Metrica WRITE-token access preflight: GET ONLY.
No API mutation, no token/account identities saved. Scope to counter 113839860.
"""
from datetime import datetime, timezone
from pathlib import Path
import json, os, urllib.error, urllib.request, sys
C=113839860
ROOT="https://api-metrika.yandex.net"
OUT=Path("data/direct_optimization_20261120/metrika_write_access_check.json")
def utc():return datetime.now(timezone.utc).isoformat()
def save(d):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def get(path, token):
    if path not in (f"/management/v1/counter/{C}",
                    f"/management/v1/counter/{C}/goals",
                    f"/management/v1/counter/{C}/grants"):
        raise RuntimeError("Outside read-only allowlist")
    req=urllib.request.Request(ROOT+path,method="GET",
                headers={"Authorization":"OAuth "+token,"Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=35) as res:
            if res.status!=200:raise RuntimeError("Unexpected status")
            raw=res.read(1000000)
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        raise RuntimeError("Metrica read "+path.rsplit("/",1)[-1]+" HTTP "+str(code)) from None
    except Exception as e:
        raise RuntimeError("Metrica read transport error "+type(e).__name__) from None
    return json.loads(raw)
def main():
    token=os.getenv("YANDEX_METRIKA_WRITE_TOKEN","")
    result={"status":"started","counter_id":C,"started_utc":utc(),
            "token_present":bool(token),"mutation_attempts":0,"checks":{},"errors":[]}
    save(result)
    if not token:
        result["status"]="no_write_token"
        result["finished_utc"]=utc()
        save(result)
        print("Metrica write credential is NOT available in Github Actions environment.")
        return 0
    try:
        c=get(f"/management/v1/counter/{C}",token).get("counter") or {}
        if c.get("id")!=C:raise RuntimeError("Wrong counter ID")
        result["checks"]["counter"]={"status":"ok",
              "permission":c.get("permission"),
              "active":c.get("status"),
              "site_matches":("leoparts-akpp.ru" in str(c.get("site") or ""))}
    except Exception as e:
        result["checks"]["counter"]={"status":"error","error":str(e)[:180]}
        result["errors"].append("counter")
    try:
        items=get(f"/management/v1/counter/{C}/goals",token).get("goals")
        if not isinstance(items,list):raise RuntimeError("Missing goals array")
        watched={675506966,675507028}
        present={x.get("id") for x in items}
        checkout=[x for x in items if x.get("name")=="LEOPARTS | Начало оплаты (не покупка)"]
        result["checks"]["goals"]={"status":"ok","total_count":len(items),
              "priority_goals_present":watched.issubset(present),
              "checkout_start_goal_present":bool(checkout)}
    except Exception as e:
        result["checks"]["goals"]={"status":"error","error":str(e)[:180]}
        result["errors"].append("goals")
    try:
        d=get(f"/management/v1/counter/{C}/grants",token)
        if not isinstance(d.get("grants"),list):
            raise RuntimeError("No permissions list")
        result["checks"]["grants"]={"status":"ok","list_access":True}
    except Exception as e:
        result["checks"]["grants"]={"status":"not_confirmed","reason":str(e)[:180]}
    permission=(result["checks"].get("counter") or {}).get("permission")
    result["status"]=("readable_and_account_edit_permission" if permission in ("own","edit")
                      and not result["errors"] else
                      "readable_but_edit_permission_unconfirmed" if not result["errors"]
                      else "access_error")
    result["write_scope_note"]="GET permission indicates counter rights, but metrika:write OAuth scope is confirmed only by an authorized write operation."
    result["finished_utc"]=utc()
    save(result)
    print("Metrica write-token PRECHECK:",result["status"],"permission",permission,
          "goals",(result["checks"].get("goals") or {}).get("total_count"),flush=True)
    return 0
if __name__=="__main__":sys.exit(main())
