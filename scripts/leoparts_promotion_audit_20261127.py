"""Read-only promotion audit. No marketplace mutations. Exact-SKU identifiers."""
import json, csv, math, re, subprocess, time
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
import openpyxl
import ozon_client as oz
import wb_client as wb
from config import config

OUT=Path("data/promotion_audit_20261127")
OUT.mkdir(parents=True,exist_ok=True)
MOS=ZoneInfo("Europe/Moscow")
TODAY=datetime.now(MOS).date()
END=TODAY-timedelta(days=1)
def now():return datetime.now(timezone.utc).isoformat()
def load(p):return json.loads(Path(p).read_text())
def save(name,v): (OUT/name).write_text(json.dumps(v,ensure_ascii=False,indent=1,default=str)+"\n")
def git(*args):return subprocess.check_output(["git",*args],stderr=subprocess.DEVNULL)
def money(v):
    if isinstance(v,dict):
        if "amount" in v:return money(v["amount"])
        if "units" in v:
            a=money(v["units"]);n=money(v.get("nanos",0))
            return a+n/1e9 if a is not None and n is not None else None
        return None
    if isinstance(v,bool) or v is None:return None
    try:
        f=float(str(v).replace(" ","").replace(",","."))
        return f if math.isfinite(f) else None
    except (TypeError,ValueError):return None
def positive(x):
    n=money(x)
    return n if n is not None and n>0 else None
def flags(v):
    if v in (True,"ENABLED"):return True
    if v in (False,"DISABLED"):return False
    return None
def datefmt(x):return x.isoformat()
def reduce_price(row):
    p=row.get("price") or {}
    return {"sku":row.get("offer_id"),"product_id":row.get("product_id"),"price":money(p.get("price")),"min_price":money(p.get("min_price")),"old_price":money(p.get("old_price")),"seller_marketing_price":money(p.get("marketing_seller_price")),"auto":flags(p.get("auto_add_to_ozon_actions_list_enabled")),"marketing_actions":[{k:x.get(k) for k in ("title","value","date_from","date_to")} for x in (row.get("marketing_actions") or {}).get("actions",[]) if isinstance(x,dict) and re.search(r"бустинг|ч[её]рная пятница|скидка на сток|распродажа|11\.11",str(x.get("title")),re.I)],"commissions":row.get("commissions"),"acquiring":row.get("acquiring"),"price_indexes":row.get("price_indexes")}
def summary(rows):
    return {"count":len(rows),"auto_enabled":sum(x["auto"] is True for x in rows),"auto_disabled":sum(x["auto"] is False for x in rows),"auto_unknown":sum(x["auto"] is None for x in rows),"min_equals_price":sum(positive(x["price"]) is not None and x["min_price"]==x["price"] for x in rows),"min_above_price":sum(x["price"] is not None and x["min_price"] is not None and x["min_price"]>x["price"] for x in rows),"named_boosting":sum(bool(x["marketing_actions"]) for x in rows)}
def redacted_error(e):return type(e).__name__+": "+str(e).splitlines()[0][:300]
report={"started_utc":now(),"mode":"read_only","marketplace_writes":0,"errors":[]}
# Historical snapshots: only actual saved API fields, never infer continuous state.
hist=[]
try:
    lines=git("log","--format=%H %aI","--since=2026-08-01","--","data/ozon_products.json").decode().splitlines()
    daily={}
    for l in lines:
        sha,t=l.split(" ",1)
        d=datetime.fromisoformat(t).astimezone(MOS).date().isoformat()
        daily.setdefault(d,(sha,t))
    for d,(sha,t) in sorted(daily.items()):
        try:
            j=json.loads(git("show",sha+":data/ozon_products.json"))
            live={r["offer_id"]:r for r in j.get("list",[]) if r.get("offer_id") and not r.get("archived")}
            rows=[reduce_price(x) for x in j.get("details",[]) if x.get("offer_id") in live]
            if not rows:continue
            hist.append({"date":d,"commit":sha,"commit_time":t,"summary":summary(rows),"rows":rows})
        except Exception as e:report["errors"].append({"section":"historical_snapshot","date":d,"error":redacted_error(e)})
    save("ozon_price_promo_history.json",hist)
    report["historical_snapshots"]=[{k:h[k] for k in ("date","commit","commit_time","summary")} for h in hist]
except Exception as e:report["errors"].append({"section":"history","error":redacted_error(e)})

# Preserve actual master prices/costs from distinct commits around intervention and recent changes.
def read_master(blob):
    from io import BytesIO
    ws=openpyxl.load_workbook(BytesIO(blob),read_only=True,data_only=True)["Товары"]
    it=ws.iter_rows(values_only=True);headers=[str(x or "").strip() for x in next(it)]
    out={}
    for row in it:
        if not row or not row[0]:continue
        out[str(row[0]).strip()]={k:row[i] for i,k in enumerate(headers) if i<len(row) and (k=="Цена, ₽" or k=="Закупка, ₽" or k=="Название товара" or re.search(r"цен[аы].*(WB|Ozon|ВБ|Маркет)|^(WB|Ozon|ВБ|Маркет).*цен",k,re.I))}
    return out
try:
    prev=None;mh=[]
    for d in ("2026-08-31","2026-09-24","2026-10-07","2026-10-12","2026-10-19","2026-10-20","2026-10-26","2026-10-27","2026-10-28","2026-11-03","2026-11-12","2026-11-19","2026-11-20","2026-11-23","2026-11-24","2026-11-26"):
        l=git("log","-1","--format=%H %aI","--before="+d+"T23:59:59+03:00","--","data/master_control.xlsx").decode().strip()
        if not l:continue
        sha,t=l.split(" ",1)
        if sha==prev:continue
        prev=sha
        mh.append({"date":d,"commit":sha,"commit_time":t,"rows":read_master(git("show",sha+":data/master_control.xlsx"))})
    save("master_price_history.json",mh)
    report["master_snapshots"]=len(mh)
except Exception as e:report["errors"].append({"section":"master_history","error":redacted_error(e)})
# Historical order windows; archived snapshots have exact semantics & observation date.
data={}
for n in ("ozon_fbs","ozon_fbo","wb","market","avito","ozon_finance"):
    try:data[n]=load("data/crisis_audit_20261124/"+n+".json")
    except Exception as e:report["errors"].append({"section":n,"error":redacted_error(e)})
def totals(rows,a,b):
    s=defaultdict(float)
    for d,r in rows.items():
        if a<=d<=b:
            for k,v in r.items():
                if isinstance(v,(float,int)) and not isinstance(v,bool):s[k]+=v
    return {k:round(v,2) for k,v in s.items()}
windows=[]
for pivot in (date(2026,10,12),date(2026,10,19),date(2026,10,21),date(2026,10,27)):
    for length in (7,14,28):
        a=(pivot-timedelta(days=length)).isoformat();b=(pivot-timedelta(days=1)).isoformat()
        c=pivot.isoformat();d=(pivot+timedelta(days=length-1)).isoformat()
        if d>"2026-11-23":continue
        windows.append({"pivot":c,"days":length,"before":[a,b],"after":[c,d],"channels":{n:{"before":totals(j["daily"],a,b),"after":totals(j["daily"],c,d)} for n,j in data.items()}})
save("historical_windows.json",windows)
# Full assortment live snapshot. Use only explicitly allowed read endpoints.
allowed_oz_get={"/v1/actions"}
allowed_oz_post={"/v3/product/list","/v5/product/info/prices","/v2/actions/products","/v2/actions/candidates","/v1/actions/products"}
allowed_wb={"/api/v2/list/goods/filter","/api/v1/calendar/promotions","/api/v1/calendar/promotions/details","/api/v1/calendar/promotions/nomenclatures"}
calls=Counter()
def query(platform,method,path,body=None,params=None):
    if platform=="ozon":
        if path not in (allowed_oz_get if method=="GET" else allowed_oz_post if method=="POST" else set()):raise ValueError("Read allowlist violation")
        base=config.ozon_api_base;headers=oz._headers()
    else:
        if method!="GET" or path not in allowed_wb:raise ValueError("Read allowlist violation")
        base=config.wb_prices_base if path.startswith("/api/v2/") else "https://dp-calendar-api.wildberries.ru";headers=wb._headers()
    for attempt in range(3):
        calls[platform+" "+path]+=1
        res=requests.request(method,base+path,headers=headers,json=body if method=="POST" else None,params=params,timeout=25,allow_redirects=False)
        if res.status_code==429 and attempt<2:
            time.sleep(min(60,max(2,int(res.headers.get("Retry-After","3")))));continue
        if not res.ok:raise RuntimeError(f"{platform} {path}: HTTP {res.status_code} "+res.text[:220])
        return res.json()
def oz_list():
    out=[];cursor="";seen=set()
    for _ in range(50):
        j=query("ozon","POST","/v3/product/list",{"filter":{"visibility":"ALL"},"last_id":cursor,"limit":100}).get("result",{})
        rows=j.get("items",[]);out+=rows;cursor=j.get("last_id") or ""
        if not rows or not cursor:break
        if cursor in seen:raise RuntimeError("repeated product cursor")
        seen.add(cursor)
    return out
def action_rows(path,aid):
    out=[];cursor="";seen=set()
    for _ in range(50):
        j=query("ozon","POST",path,{"action_id":aid,"limit":100,"last_id":cursor})
        root=j.get("result") or j
        rows=root.get("products") or root.get("items") or []
        if not isinstance(rows,list):raise ValueError("unexpected promo rows")
        out.extend(rows)
        nxt=root.get("last_id")
        if not rows:break
        if not nxt:
            if len(rows)>=100 and (root.get("total") is None or len(out)<int(root["total"])):raise RuntimeError("no cursor; result incomplete")
            break
        if nxt in seen:raise RuntimeError("repeated cursor")
        seen.add(nxt);cursor=nxt
    return out
def compact_action(p):
    # Preserve price structures, boost, flags; no buyer data.
    return {k:v for k,v in p.items() if k in ("id","product_id","offer_id","sku","price","action_price","max_action_price","marketplace_seller_price","min_seller_price","add_mode","stock","min_stock","recommended_stock","alert_max_action_price","failed","current","min","max_boost","price_min_elastic","price_max_elastic","is_quarantined","website_prices","boosting","boost","boosting_info","elastic_boosting","is_participating","price_range","price_min","price_max")}
ozrows=[];promos=[]
try:
    products=oz_list();live={p["offer_id"]:p for p in products if not p.get("archived") and p.get("offer_id")}
    pid={str(p["product_id"]):p["offer_id"] for p in products if p.get("product_id")}
    q=query("ozon","POST","/v5/product/info/prices",{"filter":{"offer_id":list(live)},"limit":len(live)})
    ozrows=[{**reduce_price(p),"has_fbs_stocks":live.get(p.get("offer_id"),{}).get("has_fbs_stocks"),"has_fbo_stocks":live.get(p.get("offer_id"),{}).get("has_fbo_stocks")} for p in (q.get("result") or q).get("items",[]) if p.get("offer_id") in live]
    report["ozon_live"]={"checked_utc":now(),"catalog_active":len(live),"price_rows":len(ozrows),"summary":summary(ozrows),"missing_prices":sorted(set(live)-{p["sku"] for p in ozrows})}
    save("ozon_live_prices.json",ozrows)
    actions=query("ozon","GET","/v1/actions").get("result",[])
    for a in actions:
        aid=a.get("id")
        if not aid:continue
        rec={"action":a,"sections":{}}
        for kind,path in (("participants","/v2/actions/products"),("candidates","/v2/actions/candidates")):
            try:
                vals=action_rows(path,aid)
                rec["sections"][kind]={"status":"ok","rows":[dict(compact_action(p),mapped_offer_id=pid.get(str(p.get("id") or p.get("product_id")))) for p in vals],"raw_keys":sorted({k for p in vals for k in p})}
            except Exception as e:rec["sections"][kind]={"status":"error","error":redacted_error(e)}
        promos.append(rec)
    save("ozon_live_promotions.json",promos)
    report["ozon_actions"]=[{"id":r["action"].get("id"),"title":r["action"].get("title"),"date_start":r["action"].get("date_start"),"date_end":r["action"].get("date_end"),"declared_participating":r["action"].get("participating_products_count"),"potential":r["action"].get("potential_products_count"),"sections":{k:{"status":v["status"],"count":len(v.get("rows",[])),"error":v.get("error"),"raw_keys":v.get("raw_keys")} for k,v in r["sections"].items()}} for r in promos]
except Exception as e:report["errors"].append({"section":"ozon_live","error":redacted_error(e)})
wbrows=[];wbpromos=[]
try:
    root=query("wb","GET","/api/v2/list/goods/filter",params={"limit":1000,"offset":0})
    wbrows=(root.get("data") or {}).get("listGoods",[])
    save("wb_live_prices.json",wbrows)
    cards=load("data/wb_cards.json").get("cards",[])
    codes={str(x.get("nmID")):x.get("vendorCode") for x in cards}
    start=(TODAY-timedelta(days=8)).isoformat()+"T00:00:00Z";end=(TODAY+timedelta(days=40)).isoformat()+"T23:59:59Z"
    result=query("wb","GET","/api/v1/calendar/promotions",params={"startDateTime":start,"endDateTime":end,"allPromo":"true","limit":1000,"offset":0})
    acts=(result.get("data") or {}).get("promotions",[])
    if not isinstance(acts,list):raise ValueError("unknown WB promotions shape")
    for a in acts[:60]:
        aid=a.get("id");rec={"action":a,"sections":{}}
        if not aid:continue
        try:
            rec["details"]=query("wb","GET","/api/v1/calendar/promotions/details",params={"promotionIDs":aid}).get("data")
        except Exception as e:rec["details_error"]=redacted_error(e)
        time.sleep(.65)
        # API does not list auto-promotion products; do not infer exclusion from missing results.
        for included in (True,False):
            kind="participants" if included else "candidates"
            try:
                x=query("wb","GET","/api/v1/calendar/promotions/nomenclatures",params={"promotionID":aid,"inAction":str(included).lower(),"limit":1000,"offset":0})
                v=(x.get("data") or {}).get("nomenclatures",[])
                rec["sections"][kind]={"status":"ok","rows":[dict(p,mapped_offer_id=codes.get(str(p.get("id") or p.get("nmID")))) for p in v],"data_keys":list((x.get("data") or {}).keys())}
            except Exception as e:rec["sections"][kind]={"status":"error","error":redacted_error(e)}
            time.sleep(.65)
        wbpromos.append(rec)
    save("wb_live_promotions.json",wbpromos)
    report["wb_live"]={"checked_utc":now(),"price_rows":len(wbrows),"actions":len(acts),"queried_actions":len(wbpromos)}
    report["wb_actions"]=[{"action":x["action"],"sections":{k:{"status":v["status"],"count":len(v.get("rows",[])),"error":v.get("error"),"data_keys":v.get("data_keys")} for k,v in x["sections"].items()}} for x in wbpromos]
except Exception as e:report["errors"].append({"section":"wb_live","error":redacted_error(e)})

# All-SKU financial scenario using previously verified procurement records and recent actual accruals.
# This is conservative screening, not net-profit proof or an approved sale price.
master=read_master(Path("data/master_control.xlsx").read_bytes())
decision=load("data/leoparts_pricing_decision_20261124/decision.json")
costref={r["sku"]:r for r in decision.get("all_products",[])}
finance=load("data/ozon_finance.json").get("items",{})
live_prices={p["sku"]:p for p in ozrows}
cand=defaultdict(list)
for entry in promos:
    a=entry["action"]
    for group in ("participants","candidates"):
        for p in entry["sections"].get(group,{}).get("rows",[]):
            oid=p.get("mapped_offer_id") or p.get("offer_id")
            if not oid:continue
            cand[oid].append({"action_id":a.get("id"),"title":a.get("title"),"date_start":a.get("date_start"),"date_end":a.get("date_end"),"group":group,**{k:p.get(k) for k in ("action_price","max_action_price","price","min_seller_price","stock","is_quarantined","price_min_elastic","price_max_elastic","current","min","max_boost")}})
def model(p,c,f):
    if not p or not c or not f:return None
    sold=money(f.get("sold")) or 0;returned=money(f.get("returned")) or 0
    # Returned transactions and other period fees can distort realized unit rates.
    # Use only clean recent rows, with >=3 units, and do not give write-ready recommendations.
    if sold<3 or returned:return None
    rev=positive(f.get("revenue"));net=money(f.get("net"))
    if rev is None or net is None:return None
    co=p.get("commissions") or {}
    schema=[k for k in ("fbs","fbo") if p.get("has_"+k+"_stocks")]
    if not schema:return None
    comms=[money(co.get("sales_percent_"+k)) for k in schema]
    if any(x is None or x<0 or x>100 for x in comms):return None
    comm=max(comms)/100
    logs=[]
    for k in schema:
        l=money(co.get(k+"_direct_flow_trans_max_amount"))
        last=money(co.get(k+"_deliv_to_customer_amount"))
        first=money(co.get(k+"_first_mile_max_amount")) if k=="fbs" else 0
        if l is None or last is None or first is None:return None
        logs.append(max(0,l)+max(0,last)+max(0,first))
    actual_comm=-(money(f.get("commission")) or 0)/rev
    acq=money(p.get("acquiring"));cur=positive(p.get("price"))
    if acq is None or cur is None:return None
    # Actual observed commission, advertised current commission and acquiring,
    # 6% tax scenario, 5 percentage point risk reserve; max historical services
    # and current maximum delivery. No guaranteed floor, excludes unpriced costs.
    variable=max(comm,actual_comm)+max(0,acq/cur)+.06+.05
    fixed=max(max(logs),max(0,-(money(f.get("logistics")) or 0)/sold))+max(0,-(money(f.get("services")) or 0)/sold)+50
    if variable>=1:return None
    # 50 ₽ packaging + safety assumption; retain owner-approved floor later.
    return {"variable_fraction":round(variable,5),"fixed_scenario":round(fixed,2),"break_even_scenario":math.ceil((c+fixed)/(1-variable)),"history_sold":sold,"history_revenue":rev,"history_net":net,"historical_margin_before_unknown":round((net-.06*rev-c*sold)/sold,2),"assumption":"No returns in historical period, procurement current, 50 RUB package, 5 pp reserve; advertising/shared fees/import/warranty may be incomplete. NOT an approved floor."}
matrix=[]
for sku in sorted(set(master)|set(live_prices)):
    row=master.get(sku,{})
    cost=positive(row.get("Закупка, ₽"))
    prior=costref.get(sku,{})
    priorcost=positive(prior.get("cost_floor"))
    effective=max(cost or 0,priorcost or 0) if cost else None
    p=live_prices.get(sku)
    f=finance.get(sku)
    econ=model(p,effective,f)
    actions=[]
    for a in cand.get(sku,[]):
        threshold=positive(a.get("max_action_price")) or positive(a.get("action_price"))
        # Keep source fields for verification, never use undocumented candidate semantics as a write target.
        margin=None
        if econ and threshold:
            margin=round(threshold*(1-econ["variable_fraction"])-effective-econ["fixed_scenario"],2)
        actions.append({**a,"threshold_scenario":threshold,"margin_scenario_at_threshold":margin,"status":"REVIEW_ONLY"})
    before=totals(data.get("ozon_fbs",{}).get("sku_daily",{}).get(sku,{}),"2026-09-29","2026-10-26").get("units",0)+totals(data.get("ozon_fbo",{}).get("sku_daily",{}).get(sku,{}),"2026-09-29","2026-10-26").get("units",0)
    after=totals(data.get("ozon_fbs",{}).get("sku_daily",{}).get(sku,{}),"2026-10-27","2026-11-23").get("units",0)+totals(data.get("ozon_fbo",{}).get("sku_daily",{}).get(sku,{}),"2026-10-27","2026-11-23").get("units",0)
    matrix.append({"sku":sku,"title":row.get("Название товара"),"master_price":money(row.get("Цена, ₽")),"master_cost":cost,"conservative_cost_reference":effective,"cost_floor_previous_audit":priorcost,"live_price":p.get("price") if p else None,"live_min_price":p.get("min_price") if p else None,"auto":p.get("auto") if p else None,"fbs":p.get("has_fbs_stocks") if p else None,"fbo":p.get("has_fbo_stocks") if p else None,"units_before":before,"units_after":after,"finance":f,"scenario":econ,"actions":actions,"classification":"REVIEW_ONLY" if econ else "INSUFFICIENT_COST_OR_FINANCE_OR_STOCK"})
save("all_sku_review.json",matrix)
with (OUT/"all_sku_review.csv").open("w",newline="",encoding="utf-8-sig") as f:
    keys=["sku","title","master_price","master_cost","conservative_cost_reference","live_price","live_min_price","auto","fbs","fbo","units_before","units_after","scenario_break_even","action_count","classification"]
    w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
    for r in matrix:w.writerow({**{k:r.get(k) for k in keys},"scenario_break_even":(r.get("scenario") or {}).get("break_even_scenario"),"action_count":len(r["actions"])})
report["matrix"]={"rows":len(matrix),"live_prices":len(live_prices),"modelled":sum(r["scenario"] is not None for r in matrix),"missing_cost":sum(r["master_cost"] is None for r in matrix),"action_rows":sum(len(r["actions"]) for r in matrix),"classification_counts":dict(Counter(r["classification"] for r in matrix))}
report["read_api_calls"]=dict(calls)
report["finished_utc"]=now()
save("registry.json",report)
print(json.dumps({k:v for k,v in report.items() if k not in ("historical_snapshots","ozon_actions","wb_actions")},ensure_ascii=False,default=str),flush=True)
