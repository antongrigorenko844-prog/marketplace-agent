"""LEOPARTS with-board responsive ad specificity: guarded one-ad text-only patch.
No ad status, other groups, feed, price, campaign, keyword or budget updates.
"""
import json,os,sys,urllib.request,urllib.error
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
CID=716316525
GROUP=5826183946
AID=1927466206584354174
BASE="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_recovery_20261122/with_board_copy_fix.json")
TITLE="Мехатроник DSG7 DQ200 с платой"
TEXT="В сборе с платой 0AM927769. Проверен на авто. Подбор по VIN, доставка по РФ."
GET={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status","StatusClarification"],
    "ResponsiveAdFieldNames":["Titles","Texts","Href","SitelinkSetId","SitelinksModeration"],
    "Page":{"Limit":1000}}
PLANNED={"Ads":[{"Id":AID,"ResponsiveAd":{"Titles":[TITLE],"Texts":[TEXT]}}]}
WRITE_ONCE=False
def now():return datetime.now(timezone.utc).isoformat()
def save(o):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    q=OUT.with_suffix(".tmp");q.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");q.replace(OUT)
def request(method,payload):
    global WRITE_ONCE
    if method=="get" and payload==GET:pass
    elif method=="update" and payload==PLANNED and not WRITE_ONCE:WRITE_ONCE=True
    else:raise RuntimeError("Unapproved or repeated API request")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Direct token unavailable")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(BASE+"ads",method="POST",headers=headers,
      data=json.dumps({"method":method,"params":payload},ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req,timeout=55) as resp:
            blob=resp.read(2*1024*1024+1)
            if len(blob)>2*1024*1024:raise RuntimeError("Oversize response")
            obj=json.loads(blob)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Ads "+method+" HTTP "+str(code)) from None
    except Exception:raise RuntimeError("Ads "+method+" transport/response failure") from None
    if obj.get("error"):raise RuntimeError("Ads API error "+str(obj["error"].get("error_code")))
    data=obj.get("result")
    if not isinstance(data,dict) or data.get("LimitedBy"):raise RuntimeError("Ads read incomplete")
    return data
def ads():
    rows=request("get",GET).get("Ads")
    if not isinstance(rows,list) or len(rows)<5 or len(rows)>25 or len({x.get("Id") for x in rows})!=len(rows):
        raise RuntimeError("Ad inventory unexpectedly changed")
    if any(x.get("CampaignId")!=CID for x in rows):
        raise RuntimeError("Cross-campaign ad response")
    return sorted(rows,key=lambda x:x["Id"])
def titles(ad):return [x.get("Title") for x in (ad.get("ResponsiveAd") or {}).get("Titles") or []]
def texts(ad):return [x.get("Text") for x in (ad.get("ResponsiveAd") or {}).get("Texts") or []]
def stable(a):
    a=deepcopy(a)
    a.pop("Status",None);a.pop("StatusClarification",None)
    if a.get("ResponsiveAd"):a["ResponsiveAd"].pop("SitelinksModeration",None)
    return a
def main():
    result={"status":"preflight","campaign":CID,"group":GROUP,"ad_id":AID,"mode":"text_only",
            "write_attempted":False,"started_utc":now(),"errors":[]}
    save(result)
    try:
        if len(TITLE)>56 or len(TEXT)>81:raise RuntimeError("Overlong ad copy")
        before=ads();b={x["Id"]:x for x in before}
        ad=b.get(AID)
        if (not ad or ad.get("AdGroupId")!=GROUP or ad.get("Type")!="RESPONSIVE_AD"
            or ad.get("State")!="ON" or ad.get("Status") not in ("ACCEPTED","MODERATION")):
            raise RuntimeError("Target ad not approved or changed")
        actual_t,actual_x=titles(ad),texts(ad)
        result.update(before_title=actual_t,before_text=actual_x,landing=(ad.get("ResponsiveAd") or {}).get("Href"),
                      new_title=TITLE,new_text=TEXT,ads_count=len(before))
        if actual_t==[TITLE] and actual_x==[TEXT]:
            result["status"]="already_exact_copy";return 0
        if actual_t!=["Мехатроник DSG7 DQ200"] or actual_x!=["Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ"]:
            raise RuntimeError("Unexpected current ad text; do not overwrite")
        if not result["landing"] or not result["landing"].startswith("https://leoparts-akpp.ru/mehatronik-dq200"):
            raise RuntimeError("Unexpected landing URL")
        save(result)
        refreshed=ads()
        if refreshed!=before:raise RuntimeError("Ad changed before update")
        result["write_attempted"]=True;result["started_update_utc"]=now();save(result)
        try:
            out=request("update",PLANNED)
            rows=out.get("UpdateResults") or []
            result["api_accepted"]=len(rows)==1 and rows[0].get("Id")==AID and not rows[0].get("Errors")
        except Exception as e:result["errors"].append(str(e)[:200])
        after=ads();z={x["Id"]:x for x in after}
        other=all(stable(b[k])==stable(z[k]) for k in b if k!=AID)
        expected=stable(b[AID]);received=stable(z[AID])
        expected["ResponsiveAd"]["Titles"]=[{"Title":TITLE,"Status":"ACCEPTED"}]
        expected["ResponsiveAd"]["Texts"]=[{"Text":TEXT,"Status":"ACCEPTED"}]
        # New ad text may be in moderation; ignore item-level moderation status during comparison.
        for group in ("Titles","Texts"):
            for row in expected["ResponsiveAd"][group]:row.pop("Status",None)
            for row in received["ResponsiveAd"][group]:row.pop("Status",None)
        result.update(after_title=titles(z[AID]),after_text=texts(z[AID]),other_ads_unchanged=other,
                      target_other_fields_unchanged=(expected==received),verified_utc=now())
        ok=(result["after_title"]==[TITLE] and result["after_text"]==[TEXT] and other and expected==received)
        result["status"]="applied_verified" if ok else "requires_manual_review"
        return 0 if ok else 1
    except Exception as e:
        result["status"]="blocked_or_unverified";result["errors"].append(str(e)[:250]);return 1
    finally:
        result["finished_utc"]=now();save(result)
        print("With-board ad copy",result["status"],"attempted",result["write_attempted"],flush=True)
if __name__=="__main__":sys.exit(main())
