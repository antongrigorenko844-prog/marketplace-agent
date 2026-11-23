"""Bounded owner-approved semantic negatives. Preserve all existing exclusions and settings.
Literal/morphological screening is conservative, not a complete Direct matching emulator."""
import json,re,sys,subprocess
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parent))
from direct_mass_negatives_apply_20261120 import Client,campaign_read,phrases,stable,atomic_save,now,CID,GETS,GROUP_GET,ADS_GET
import direct_preflight_20261120 as direct
OUT=Path("data/direct_recovery_20261124/semantic_filter.json")
CANDIDATES=[
"записаться +в автосервис","запись +на ремонт","стоимость работ +по ремонту","стоимость нормочаса","цена нормочаса",
"выезд автомеханика","выездная диагностика","вызов мастера +на дом","телефон автосервиса","адрес автосервиса","услуги автоэлектрика",
'"ремонт под ключ"',"сколько стоит работа +по замене",
'"автокаталог запчастей"','"магазин автозапчастей"','"автомагазин рядом"','"каталог запчастей +по vin"','"подбор запчастей +по вин"','"рынок автозапчастей"',
"скачать каталог запчастей","скачать приложение автозапчасти","программа учета автозапчастей","программа +для магазина автозапчастей",
'"акпп dq200 купить"','"коробка дсг 7 цена новой"','"купить коробку робот"','"коробка передач dq200 +в сборе"','"контрактная коробка dq200"','"купить акпп +в сборе"']
PROTECTED=["купить мехатроник dq200 без платы","мехатроник dsg7 с платой купить","плата мехатроника 0am927769d","запчасти для ремонта мехатроника dq200","подбор мехатроника по vin","шток сцепления dq200 купить","ремкомплект мехатроника","гидроплита мехатроника","втулка штока 0am325025d","инструмент для ремонта мехатроника","запчасти dsg оптом для автосервиса","плата мехатроника с прошивкой купить"]
def tokens(s):return re.findall(r"[a-zа-яё0-9]+",s.lower().replace("ё","е"))
def overlap(p,q):
    a,b=tokens(p),tokens(q)
    return set(a)<=set(b) and (not p.startswith('"') or len(a)==len(b))
def cost(xs):return sum(len(re.sub(r'[\s\-!+"\[\]]','',x)) for x in xs)
def main():
    if OUT.exists():raise RuntimeError("Existing journal; no replay")
    j={"campaign":CID,"status":"preflight","started_utc":now(),"attempted":False}
    atomic_save(OUT,j)
    api=Client("apply")
    try:
        before=campaign_read(api);old=phrases(before)
        keys=direct.keywords()
        groups=api("adgroups","get",GROUP_GET).get("AdGroups",[])
        ads=api("ads","get",ADS_GET).get("Ads",[])
        if len(keys)!=272 or len(groups)!=6 or len(ads)!=8 or before.get("State")!="ON":raise RuntimeError("Campaign inventory drift")
        expected=json.loads(Path("data/direct_recovery_20261124/exact_negatives.json").read_text())["after"]
        if set(old)!=set(expected):raise RuntimeError("Negative list changed since verified snapshot")
        manual=[x["Keyword"] for x in keys if x["Keyword"]!="---autotargeting"]
        protected=manual+PROTECTED
        add=[];blocked=[]
        for p in CANDIDATES:
            if p in old:continue
            conflicts=[q for q in protected if overlap(p,q)]
            if conflicts:blocked.append({"phrase":p,"conflicts":conflicts});continue
            if not 1<=len(tokens(p))<=7:raise RuntimeError("Invalid candidate")
            add.append(p)
        target=old+add
        if cost(target)>19000:raise RuntimeError("Reserve margin exceeded")
        j.update(before=old,additions=add,blocked=blocked,manual_keywords_count=len(manual),manual_keywords=manual,before_chars=cost(old),after_chars=cost(target),groups_before=groups,ads_before=ads,existing_risk_phrases=[p for p in old if p in ["сцепление","прошивка мехатроника","снятие мехатроника","установка мехатроника","оборудование ремонта кпп","тестовый стенд","стенд !для проверки мехатроника"]])
        if not add:j["status"]="already_present";return
        if campaign_read(api)!=before or direct.keywords()!=keys:raise RuntimeError("Concurrent edit")
        api.allowed_update={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":target}}]}
        j["attempted"]=True;atomic_save(OUT,j)
        subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True,timeout=180)
        try:j["api_result"]=api("campaigns","update",api.allowed_update)
        except Exception as e:j["api_error"]=str(e)
        after=campaign_read(api);b=deepcopy(before);a=deepcopy(after)
        b.pop("NegativeKeywords",None);a.pop("NegativeKeywords",None)
        j.update(after=phrases(after),other_settings_unchanged=stable(a)==stable(b),keywords_unchanged=stable(direct.keywords())==stable(keys),groups_unchanged=stable(api("adgroups","get",GROUP_GET).get("AdGroups",[]))==stable(groups),ads_unchanged=stable(api("ads","get",ADS_GET).get("Ads",[]))==stable(ads))
        j["status"]="verified" if set(phrases(after))==set(target) and all(j[k] for k in ["other_settings_unchanged","keywords_unchanged","groups_unchanged","ads_unchanged"]) else "requires_review"
    except Exception as e:j.update(status="blocked_or_unverified",error=str(e))
    finally:j["finished_utc"]=now();atomic_save(OUT,j)
    if j["status"] not in ("verified","already_present"):raise RuntimeError(j["status"])
if __name__=="__main__":main()
