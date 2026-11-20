"""Read-only published Tilda form + Metrica goal schema investigation.
No form submissions, no CMS changes, no OAuth token in output.
"""
from datetime import datetime,timezone
from pathlib import Path
import json,os,re,sys,urllib.error,urllib.request
COUNTER=113839860
GOAL=675272284
OUT=Path("data/direct_optimization_20261120/form_tracking_structure.json")
SITE="https://leoparts-akpp.ru/mehatronik-dq200"
def utc():return datetime.now(timezone.utc).isoformat()
def persist(doc):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def metrika():
    t=os.getenv("YANDEX_METRIKA_TOKEN")
    if not t:raise RuntimeError("No Metrica token")
    req=urllib.request.Request("https://api-metrika.yandex.net/management/v1/counter/113839860/goals",
        headers={"Authorization":"OAuth "+t},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:d=json.load(resp)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close();raise RuntimeError("Metrica goal metadata HTTP "+str(code)) from None
    gs=[g for g in d.get("goals",[]) if g.get("id")==GOAL]
    if len(gs)!=1:raise RuntimeError("VIN form goal missing or duplicated")
    g=gs[0]
    whitelisted=["id","name","type","status","goal_source","auto","conditions","form_selector","form_xpath",
                  "form_id","formId","formName","selector","xpath","url","filters","parameter"]
    out={k:g.get(k) for k in whitelisted if k in g}
    return {"form_goal_properties":out,"available_keys":sorted(g),
            "note":"Goal type form does not prove a successfully accepted form submission."}
def public():
    req=urllib.request.Request(SITE,headers={"User-Agent":"Mozilla/5.0"},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=30) as resp:
            raw=resp.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise RuntimeError("Public HTML too large")
            html=raw.decode("utf-8","replace")
            status=resp.status
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Tilda public page HTTP "+str(code)) from None
    records={}
    for rid in (5246286101,5260805101):
        m=re.search(r'<div[^>]+id=["\']rec'+str(rid)+r'["\'][^>]*>',html,re.I)
        if not m:
            records[str(rid)]={"found":False};continue
        nxt=re.search(r'<div[^>]+id=["\']rec\d{7,12}["\']',html[m.end():],re.I)
        sec=html[m.start():m.end()+nxt.start() if nxt else len(html)]
        rec={
            "found":True,"is_zero_block":bool(re.search(r'data-record-type=["\']396["\']',m.group())),
            "is_t706_cart":bool(re.search(r'data-record-type=["\']706["\']',m.group())),
            "has_tn_atom_inputs":'tn-atom__inputs-data' in sec,
            "has_form_dom":bool(re.search(r'<form[\s>]',sec,re.I)),
            "has_js_form_class":'js-form-proccess' in sec,
            "contains_vin_label":bool(re.search(r'VIN|ВИН',sec,re.I)),
            "mentions_submit_virtual_url":'/tilda/form'+str(rid)+'/submitted/' in sec,
            "mentions_payment_virtual_url":'/tilda/form'+str(rid)+'/payment/' in sec,
            "has_tilda_formskey":'tilda-formskey' in sec,
            "has_tilda_recform":'data-tilda-form' in sec,
            "has_tildaform_aftersuccess":'tildaform:aftersuccess' in sec,
            "input_fields_detected":bool(re.search(r'<input[\s>]',sec,re.I)),
        }
        records[str(rid)]=rec
    return {"http_status":status,"counter_id_mentioned":str(COUNTER) in html,
            "has_ym_init":bool(re.search(r'ym\s*\(\s*'+str(COUNTER),html)),
            "has_yandex_metrica_script":'mc.yandex.ru/metrika' in html,
            "has_tilda_forms_script":'tilda-forms' in html,
            "has_tilda_cart_script":'tilda-cart' in html or "t706__" in html,
            "records":records,"raw_html_saved":False}
def main():
    out={"status":"running","mode":"read_only","site":SITE,"counter":COUNTER,
         "vin_form_goal_id":GOAL,"started_utc":utc(),
         "forms_submitted":0,"site_mutations":0,"metrika_mutations":0,"errors":[]}
    persist(out)
    for key,fn in [("form_goal_config",metrika),("site_tracking_structure",public)]:
        try:out[key]={"status":"ok","data":fn()}
        except Exception as e:
            out[key]={"status":"error","message":str(e)[:200]}
            out["errors"].append(key)
        persist(out)
    out["status"]="complete" if not out["errors"] else "partial"
    out["finished_utc"]=utc()
    persist(out)
    print("VIN form tracking structure",out["status"],"errors",out["errors"],flush=True)
if __name__=="__main__":sys.exit(main())
