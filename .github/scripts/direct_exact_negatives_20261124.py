"""Only append exact observed noncommercial queries; no other campaign edits."""
import sys, json
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parent))
from direct_mass_negatives_apply_20261120 import Client, campaign_read, phrases, stable, atomic_save, now, CID
OUT=Path("data/direct_recovery_20261124/exact_negatives.json")
ADD=[
 '"купить дсг коробку передач цена"',
 '"поиск запчастей по vin коду автомобиля"',
 '"автозапчасти пикалево"',
 '"автозапчасти прохладный"',
 '"запчасти по вин номеру"',
 '"авито авто запчасти чебоксары"',
 '"контрактные запчасти на форпост или евроавто"',
 '"ремонт дсг оренбург"',
 '"автозапчасти для иномарок рядом"',
]
def main():
    if OUT.exists():raise RuntimeError("Existing journal; no replay")
    j={"campaign":CID,"status":"preflight","started_utc":now(),"attempted":False}
    atomic_save(OUT,j)
    api=Client("apply")
    try:
        before=campaign_read(api)
        old=phrases(before)
        if before.get("State")!="ON" or len(old)<638:raise RuntimeError("Unexpected baseline")
        additions=[x for x in ADD if x not in old]
        target=old+additions
        j.update(before=old,additions=additions)
        if not additions:
            j["status"]="already_present";return
        if campaign_read(api)!=before:raise RuntimeError("Concurrent changes")
        api.allowed_update={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":target}}]}
        j["attempted"]=True;atomic_save(OUT,j)
        try:
            j["api_result"]=api("campaigns","update",api.allowed_update)
        except Exception as e:
            j["api_error"]=str(e)
        after=campaign_read(api)
        b=deepcopy(before);a=deepcopy(after)
        b.pop("NegativeKeywords",None);a.pop("NegativeKeywords",None)
        j.update(after=phrases(after),other_settings_unchanged=stable(a)==stable(b))
        j["status"]="verified" if set(phrases(after))==set(target) and j["other_settings_unchanged"] else "requires_review"
    except Exception as e:
        j.update(status="blocked_or_unverified",error=str(e))
    finally:
        j["finished_utc"]=now();atomic_save(OUT,j)
    if j["status"] not in ("verified","already_present"):raise RuntimeError(j["status"])
if __name__=="__main__":main()
