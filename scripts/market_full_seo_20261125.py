"""Yandex Market only: inspect exact active offers, category schema and OEM.
Owner approved safe confirmed fixes; preserve existing marketplace identity, grouping,
price, stock, description, brand, media and all unrelated attributes. No Ozon access."""
import sys,json,time,datetime,collections,copy,re,subprocess,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yandex_market_client as ym
from market_content import read_rows
from content_manifest import market_parameters_for
from openpyxl import load_workbook
OUT=ROOT/"data/market_full_seo_20261125.json"
BASE=ROOT/"data/market_full_seo_baseline_20261125.json"
BUSINESS=194752944
TRANS=71380208
# Exact physical part identities reviewed against attached report and previous verified catalog.
# Never assign generic transmission category to unrelated engine parts or tools.
TRANSMISSION=set("""0AM325443D 0AM927769F 0AM927769N 0am325025HX 0am325025u 0am325025z 0am325091e-set 0am325091f-set 0am325477ae 0cw0am 0cw0am325025 0cw0amdsgdq200 0cw325025h Dq200dsg7 Dq200dsg70am0cw chip-02e927770 datchik-peredach-dq200 datchik-scepleniya-dq200 derzhatel-datchikov-dq200 filtr-dsg7-dq200 gidroakkumulyator-orig korpus-filtra-0bh325159 plita-filtr-orig plita-filtr-pylniki-orig plita-filtr-salniki-orig podshipnik-vilki-6 porshni-usil-pylniki-kryshki porshni-usil-pylniki-kryshki-v2 pylniki-shtokov-orig remkomplekt-shtokov-orig salnikprivod-l salnikprivod-r salnikvhod tolkateli-salniki-alum-komplekt vtulki-shtokov-2sht""".split())
# These two products are oil-filter components for engines, not gearbox parts.
ENGINE={"EA888":"корпус","EA888gen3":"комплект","Ea888gen3":"клапан","EA888gen3-filter-kit":"комплект"}
OEM={s:s for s in ("0AM927769D","0AM927769E","0AM927769F","0AM927769G","0AM927769K","31705-1XF0C","31705-X428B","317053TX0C","31705-1XF1A","0AM325443D","0AM325433E","02E305051C","0AM325587E","0AM325219C","0AM325413A","0AM325120A","0AM301212A","0AM325467J","0AM325473","WHT001922","01X301127C","55565480")}
OEM.update({"EA888":"06L115401S","Ea888gen3":"06L115678AD","0am325091e":"0AM325091E","0am325091f":"0AM325091F","0BH325183":"0BH325183B","0BH325183 B":"0BH325183B"})
OEM_REPLACE={s:"0AM927769D" for s in ("0AM927769E","0AM927769F","0AM927769G","0AM927769K")}
# A part number for a compatible parent assembly is not an OEM number for a component.
# Only flag questionable copied numbers; don't clear fields blindly.
COMPONENTS=set("""chip-02e927770 chip-0am927769d datchik-peredach-dq200 datchik-scepleniya-dq200 derzhatel-datchikov-dq200 fpc-shleif-dq200 klipsa-datchika-peredach-dq200 kolodka-solenoidov-dq200 kryshka-platy-dq200 osnovanie-platy-dq200 shleif-platy-dq200""".split())
REVIEW_ONLY={"WHT001922":"Проверить специальную категорию автомобильного крепежа и сохранение характеристик.",
"55565480":"Импульсное кольцо распредвала: проверить категорию деталей двигателя, не датчик и не трансмиссия."}
DEFS={};TREE={}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d):
 d["updated_utc"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");tmp.replace(OUT)
def persist(d):
 save(d)
 subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def post(path,body,params=None,retries=3):
 code,d=ym._request("POST",path,params=params,json_body=body,retries=retries)
 if code!=200 or d.get("status")!="OK":raise ValueError(f"{path} HTTP {code}: {ym._errors_text(d)[:240]}")
 return d
def account():
 biz=ym.get_business_id()
 if biz!=BUSINESS:raise ValueError("Unexpected Market business ID; wrong account")
 return biz,ym._creds()[1]
def index(rows,key):
 d={}
 for r in rows:
  k=key(r)
  if not k:continue
  if k in d:raise ValueError("Duplicate exact identity "+str(k))
  d[k]=r
 return d
def get_mappings(biz,archived=False):
 rows=[];token=None;seen=set()
 for _ in range(20):
  p={"limit":100}
  if token:p["pageToken"]=token
  d=post(f"/v2/businesses/{biz}/offer-mappings",{"archived":archived},params=p)["result"]
  rows+=d.get("offerMappings",[])
  token=(d.get("paging") or {}).get("nextPageToken")
  if not token:return rows
  if token in seen:raise ValueError("Repeated pagination token")
  seen.add(token)
 raise ValueError("Pagination incomplete")
def batched_read(biz,kind,ids):
 out=[]
 for i in range(0,len(ids),80):out+=read_rows(biz,kind,ids[i:i+80])
 return out
def campaign_status(campaign,ids):
 out=[]
 for i in range(0,len(ids),80):
  d=post(f"/v2/campaigns/{campaign}/offers",{"offerIds":ids[i:i+80]})
  out+=d["result"].get("offers",[])
 return index(out,lambda x:x.get("offerId"))
def read_tree():
 def walk(n,path=()):
  if isinstance(n,list):
   for x in n:walk(x,path)
   return
  if not isinstance(n,dict):return
  nm=str(n.get("name") or "");p=path+(nm,) if nm else path
  children=n.get("children") or []
  if n.get("id"):TREE[int(n["id"])]={"path":" / ".join(p),"leaf":not children,"name":nm}
  for c in children:walk(c,p)
 if not TREE:walk(post("/v2/categories/tree",{"language":"RU"})["result"])
 if not TREE.get(TRANS) or not TREE[TRANS]["leaf"] or TREE[TRANS]["name"]!="Запчасти для трансмиссии автомобиля":raise ValueError("Transmission category not verified in current tree")
 return TREE
def schema(biz,cid):
 cid=int(cid)
 if cid not in DEFS:
  d=post(f"/v2/category/{cid}/parameters",{},params={"businessId":biz})["result"]
  if not isinstance(d.get("parameters"),list):raise ValueError("No category parameters")
  DEFS[cid]=d
 return DEFS[cid]
def wsrows(path):
 w=load_workbook(path,read_only=True,data_only=True).active
 it=iter(w.iter_rows(values_only=True));head=next(it);return {str(r[0]):dict(zip(head,r)) for r in it if r[0]}
def catid(m,c):
 a=(m.get("mapping") or {}).get("marketCategoryId")
 b=(c.get("mapping") or {}).get("marketCategoryId")
 if a and b and a!=b:raise ValueError("Category mismatch across live endpoints")
 return int(a or b or 0)
def identity(m,c):
 x=m.get("mapping") or {};z=c.get("mapping") or {}
 sku=m["offer"]["offerId"]
 if sku!=c.get("offerId"):raise ValueError("Offer ID mismatch")
 a=x.get("marketSku");b=z.get("marketSku")
 if a and b and a!=b:raise ValueError("Market SKU mismatch")
 if not (a or b):raise ValueError("No existing marketSku; no edit")
 return {"offerId":sku,"marketSku":a or b,"groupId":c.get("groupId")}
def groups(c):
 out=collections.defaultdict(list)
 for v in c.get("parameterValues") or []:out[int(v["parameterId"])].append(copy.deepcopy(v))
 return dict(out)
def canon(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def grouped_norm(c,omit=()):
 return {str(k):sorted(v,key=canon) for k,v in groups(c).items() if k not in set(omit)}
def normalize_code(v):return re.sub(r"[\s\-\u2010-\u2015]","",str(v).upper())
def same_code(a,b):return normalize_code(a)==normalize_code(b)
def text_has(t,c):return normalize_code(c) in normalize_code(t)
def definition_map(biz,cid):return {int(x["id"]):x for x in schema(biz,cid)["parameters"]}
def paramname(x):return str(x.get("name") or "")
def oem_def(defs):
 # Exact existing Ozon-style codes must never be inferred from generic part numbers.
 hits=[x for x in defs.values() if re.search(r"OEM|оригинальн.*номер",paramname(x),re.I)]
 if len(hits)!=1:return None
 return hits[0]
def existing_text(c,k):
 return [str(v.get("value") or "").strip() for v in groups(c).get(k,[]) if str(v.get("value") or "").strip()]
def valid_value(v,d):
 if v.get("valueId"):
  # Values in the current destination dictionary must exist; if list is truncated, block.
  if not any(x.get("id")==v["valueId"] for x in d.get("values",[])):return False
 elif d.get("type")=="ENUM" and not d.get("allowCustomValues"):return False
 if v.get("unitId") and v["unitId"] not in {x["id"] for x in (d.get("unit") or {}).get("units",[])}:return False
 if d.get("type")=="NUMERIC":
  try:
   n=float(v["value"]);limits=d.get("constraints") or {}
   if not limits.get("minValue",n)<=n<=limits.get("maxValue",n):return False
  except (TypeError,ValueError,KeyError):return False
 if len(str(v.get("value") or ""))>(d.get("constraints") or {}).get("maxLength",100000):return False
 return True
def dest_for(row,biz):
 s=row["sku"];current=row["category_id"]
 if s in TRANSMISSION:
  # Confirm scope via exact title + established main product family.
  title=row["name"].lower()
  if not re.search(r"dsg|dq200|dq250|dq500|мехатрон|акпп",title):return None,"Title doesn't confirm transmission"
  return TRANS,"Запчасть трансмиссии"
 if s in ENGINE:
  # Resolve current official auto-only filter/engine categories; don't guess.
  # A complete filter-containing kit fits the already verified oil filter leaf.
  if s=="EA888gen3-filter-kit":
   hits=[k for k,v in TREE.items() if v["leaf"] and v["name"]=="Масляные фильтры" and "Авто" in v["path"]]
   if len(hits)==1:return hits[0],"Комплект с фильтрующим элементом"
  if s in ("EA888","EA888gen3","Ea888gen3"):
   hits=[k for k,v in TREE.items() if v["leaf"] and v["name"]=="Система смазки двигателя" and "Авто" in v["path"]]
   if len(hits)==1:return hits[0],"Корпус/клапан масляного фильтра двигателя, не фильтрующий элемент"
  return None,"Не найден однозначный листовой тип деталей системы смазки"
 return None,REVIEW_ONLY.get(s,"Нет однозначного подтверждения иной категории")
def protected(m,c,status):
 offer=copy.deepcopy(m.get("offer") or {})
 # Category and its typed attributes are only intended mutable fields.
 for k in ("marketCategoryId","category","parameterValues","cardStatus","contentRating","contentRatingStatus","updatedAt","createdAt"):
  offer.pop(k,None)
 ident=identity(m,c)
 # Status itself may change during review; commercial fields may not.
 st={k:copy.deepcopy(v) for k,v in (status or {}).items() if k in ("basicPrice","price","purchasePrice","stock","stocks","warehouseStocks","archived","offerId","marketSku")}
 return {"identity":ident,"offer":offer,"commercial_status":st}
def make_plan(row,biz,m,c,status,master):
 current=row["category_id"];sku=row["sku"];defs=definition_map(biz,current)
 row["params"]={str(k):{"name":paramname(defs.get(k,{})),"values":v} for k,v in groups(c).items()}
 row["missing_required"]=[{"id":k,"name":paramname(v)} for k,v in defs.items() if v.get("required") and not groups(c).get(k)]
 row["missing_recommended"]=[{"id":k,"name":paramname(v)} for k,v in defs.items() if v.get("recommended") and not groups(c).get(k)]
 row["recommendations"]=c.get("recommendations",[])
 row["master_present"]=bool(master)
 row["category_plan"]={"status":"no_confirmed_change"}
 row["attribute_plan"]={"status":"no_confirmed_change","changes":[]}
 if c.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS","HAS_CARD_CAN_UPDATE_PROCESSING"):
  row["status"]="review";row["notes"].append("Карточка не имеет подтверждённого права редактирования: "+str(c.get("cardStatus")));return
 if c.get("cardStatus")=="HAS_CARD_CAN_UPDATE_PROCESSING":
  row["status"]="review";row["notes"].append("Предыдущее изменение на модерации.");return
 if not master:row["status"]="review";row["notes"].append("Точный SKU отсутствует в мастер-каталоге.");return
 if sku in REVIEW_ONLY:row["notes"].append(REVIEW_ONLY[sku])
 target,reason=dest_for(row,biz)
 if target and target!=current:
  tdefs=definition_map(biz,target);old=groups(c)
  missing=[{"id":k,"name":paramname(v)} for k,v in tdefs.items() if v.get("required") and not old.get(k)]
  removed=[{"id":k,"name":paramname(defs.get(k,{})),"values":v} for k,v in old.items() if k not in tdefs and any(x.get("value") or x.get("valueId") for x in v)]
  invalid=[{"id":k,"name":paramname(tdefs[k])} for k,vs in old.items() if k in tdefs and any(not valid_value(x,tdefs[k]) for x in vs)]
  row["category_plan"]={"status":"candidate","old":current,"new":target,"reason":reason,"destination":TREE[target]["path"],"missing_required":missing,"removed_nonempty":removed,"invalid_preserved":invalid}
  # Distinct model and all existing semantic characteristics must be preserved.
  if removed or invalid or missing:row["category_plan"]["status"]="blocked_schema"
  # Already-grouped products may be remapped or lose reviews; don't risk.
  if c.get("groupId"):row["category_plan"]["status"]="blocked_group"
  if not old:row["category_plan"]["status"]="blocked_no_attributes"
 elif target:row["category_plan"]={"status":"already_correct","reason":reason}
 elif sku in ENGINE or sku in REVIEW_ONLY:row["category_plan"]={"status":"review","reason":reason}
 oem=oem_def(defs)
 if sku in COMPONENTS and oem:
  k=int(oem["id"]);v=existing_text(c,k)
  if v:row["notes"].append("OEM компонента требует сверки: "+str(v)+"; номер родительского блока не подставлять.")
 if sku in OEM and oem:
  code=OEM[sku];k=int(oem["id"]);current_v=existing_text(c,k)
  title_master=str(master.get("Название товара") or "")
  if len(current_v)==1 and same_code(current_v[0],code):pass
  elif not text_has(row["name"],code) or not text_has(title_master,code):
   row["notes"].append("OEM не подтверждён двумя точными названиями: "+code)
  elif current_v and not (sku in OEM_REPLACE and len(current_v)==1 and same_code(current_v[0],OEM_REPLACE[sku])):
   row["notes"].append("Другой существующий OEM, отдельная проверка: "+str(current_v))
  elif not oem.get("allowCustomValues") and oem.get("type")=="ENUM":
   row["notes"].append("OEM требует значение справочника.")
  else:
   v={"parameterId":k,"value":code}
   if valid_value(v,oem):row["attribute_plan"]={"status":"candidate","changes":[v],"old":{str(k):groups(c).get(k,[])},"reason":"Номер самой детали подтверждён текущим названием и мастер-каталогом."}
 if row["category_plan"]["status"]=="candidate" or row["attribute_plan"]["status"]=="candidate":row["status"]="planned"
 elif row["notes"] or row["category_plan"]["status"].startswith("blocked") or row["category_plan"]["status"]=="review" or row["missing_required"]:row["status"]="review"
 else:row["status"]="no_confirmed_change"
def prepare():
 biz,cam=account()
 base=json.loads(BASE.read_text())
 if base.get("platform")!="Yandex Market" or base.get("business_id")!=biz:raise ValueError("Wrong report scope")
 src=index(base["rows"],lambda x:x["sku"])
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":cam,"status":"planning","external_writes":0,"registry":{},"batches":[],"errors":{},"started_utc":now(),"source_summary":base["summary"]}
 save(d)
 read_tree()
 ms=get_mappings(biz);mm=index(ms,lambda x:x["offer"].get("offerId"))
 cc=index(batched_read(biz,"offer-cards",list(mm)),lambda x:x.get("offerId"))
 sts=campaign_status(cam,list(mm));master=wsrows(ROOT/"data/master_control.xlsx")
 d["counts"]={"active_catalog":len(mm),"cards":len(cc),"campaign_status":len(sts),"report":len(src),"master":len(master)}
 d["tree_engine_candidates"]={str(k):v for k,v in TREE.items() if v["leaf"] and "Авто" in v["path"] and re.search(r"система смазки|маслян.*фильтр|запчаст.*фильтр|крепеж|крепёж|метизы|распредвал",v["name"],re.I)}
 keys=sorted(set(mm)|set(src),key=lambda s:(-(src.get(s,{}).get("impressions") or 0),s))
 for sku in keys:
  row={"sku":sku,"status":"checked","notes":[],"baseline":src.get(sku)}
  d["registry"][sku]=row
  try:
   m=mm.get(sku);c=cc.get(sku);st=sts.get(sku)
   if not m or not c:
    row["status"]="blocked_identity";row["notes"].append("Точный SKU отсутствует в одном из актуальных ответов; не подменять похожим.");continue
   offer=m["offer"];ident=identity(m,c);cid=catid(m,c)
   row.update({"name":offer.get("name"),"identity":ident,"category_id":cid,"category_path":TREE.get(cid,{}).get("path") or (m.get("mapping") or {}).get("marketCategoryName"),"content_rating":c.get("contentRating"),"card_status":c.get("cardStatus"),"campaign_status":st.get("status") if st else None,"card_errors":c.get("errors",[]),"card_warnings":c.get("warnings",[]),"source_category":src.get(sku,{}).get("source_category"),"price":offer.get("basicPrice"),"pictures":len(offer.get("pictures") or []),"has_description":bool(offer.get("description"))})
   if not cid or not TREE.get(cid,{}).get("leaf"):raise ValueError("Current leaf category absent")
   if offer.get("archived") or not st:raise ValueError("No active campaign status")
   if not offer.get("name"):raise ValueError("Missing live title")
   make_plan(row,biz,m,c,st,master.get(sku))
  except Exception as e:row["status"]="blocked_preflight";row["notes"].append(str(e)[:400])
  save(d)
 d["schema_count"]=len(DEFS)
 d["summary"]={"statuses":dict(collections.Counter(r["status"] for r in d["registry"].values())),"category_plan":dict(collections.Counter(r.get("category_plan",{}).get("status","not_checked") for r in d["registry"].values())),"attribute_plan":dict(collections.Counter(r.get("attribute_plan",{}).get("status","not_checked") for r in d["registry"].values()))}
 d["status"]="planned";persist(d)
 print(json.dumps(d["summary"],ensure_ascii=False))
def live(biz,cam,s):
 ms=read_rows(biz,"offer-mappings",[s]);cs=read_rows(biz,"offer-cards",[s]);st=campaign_status(cam,[s])
 m=index(ms,lambda r:r["offer"]["offerId"])[s];c=index(cs,lambda r:r["offerId"])[s];return m,c,st[s]
def apply():
 d=json.loads(OUT.read_text())
 biz,cam=account()
 if d["business_id"]!=biz or str(d["campaign_id"])!=str(cam) or d["status"]!="planned":raise ValueError("Unsafe state")
 read_tree()
 stop_category=False
 # groupId is the seller's variant-name field, not proof of multiple members.
 # Permit only explicitly inspected singleton groups with no lost/invalid fields.
 singleton_allow={"0cw325025h","porshni-usil-pylniki-kryshki","porshni-usil-pylniki-kryshki-v2","Ea888gen3","0cw0amdsgdq200","Dq200dsg7","0am325477ae","Dq200dsg70am0cw"}
 def groupkey(v):
  import unicodedata
  return unicodedata.normalize("NFKC",str(v or "")).strip().casefold()
 current_ids=[];current_cards=[]
 try:
  current_ids=sorted({x["offer"]["offerId"] for x in get_mappings(biz)+get_mappings(biz,True)})
  current_cards=batched_read(biz,"offer-cards",current_ids)
 except Exception as e:d["errors"]["group_recheck"]=str(e)[:300]
 group_complete=bool(current_ids) and {c.get("offerId") for c in current_cards}==set(current_ids)
 # If an archived card has no group information, do not guess.
 membership=collections.defaultdict(list)
 for c in current_cards:
  if groupkey(c.get("groupId")):membership[groupkey(c["groupId"])].append(c["offerId"])
 d["group_recheck"]={"cards":len(current_cards),"catalog_ids":len(current_ids),"singleton_candidates":[]}
 for s in sorted(singleton_allow):
  row=d["registry"].get(s)
  if not row:continue
  p=row.get("category_plan") or {}
  key=groupkey(row.get("identity",{}).get("groupId"))
  if group_complete and p.get("status")=="blocked_group" and not any(p.get(k) for k in ("missing_required","removed_nonempty","invalid_preserved")) and key and membership[key]==[s]:
   p["status"]="candidate";p["singleton_group_verified"]=True;row["status"]="planned"
   d["group_recheck"]["singleton_candidates"].append(s)
 persist(d)
 # Independent exact one-SKU writes; only documented Market content endpoint.
 for s,row in d["registry"].items():
  for kind in ("attribute","category"):
   p=row.get(kind+"_plan") or {}
   if row["status"]!="planned" or p.get("status")!="candidate" or row.get(kind+"_result"):continue
   if kind=="category" and stop_category:row[kind+"_result"]={"status":"held_after_drift"};continue
   try:
    m,c,st=live(biz,cam,s)
    if identity(m,c)!=row["identity"]:raise ValueError("Existing ID/group changed since plan")
    if c.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS"):raise ValueError("Card not currently editable")
    cid=catid(m,c)
    if cid!=row["category_id"]:raise ValueError("Category changed since plan")
    pre=protected(m,c,st);g=groups(c)
    changes=[]
    if kind=="attribute":
     target=cid;changes=p["changes"]
     for x in changes:
      k=int(x["parameterId"])
      if g.get(k,[])!=p["old"].get(str(k),[]):raise ValueError("Attribute changed since plan")
      defs=definition_map(biz,cid);definition=defs.get(k)
      if not definition or not re.search(r"OEM|оригинальн.*номер",paramname(definition),re.I) or not valid_value(x,definition):raise ValueError("Attribute outside verified scope")
     old_params=copy.deepcopy(g);expected=copy.deepcopy(g)
     for x in changes:expected[int(x["parameterId"])]=[x]
     # Existing values carried forward to avoid the observed loss on sparse updates.
     flat=[x for vs in expected.values() for x in vs]
    else:
     target=p["new"]
     if c.get("groupId") and not (p.get("singleton_group_verified") and membership[groupkey(c["groupId"])]==[s]):raise ValueError("Grouped card")
     defs=definition_map(biz,target)
     if any(k not in defs and any(x.get("value") or x.get("valueId") for x in vs) for k,vs in g.items()):raise ValueError("Migration loses existing attributes")
     flat=[x for k,vs in g.items() if k in defs for x in vs if x.get("value") or x.get("valueId")]
     if not flat or any(not valid_value(x,defs[x["parameterId"]]) for x in flat):raise ValueError("Invalid destination parameters")
     if any(v.get("required") and k not in {x["parameterId"] for x in flat} for k,v in defs.items()):raise ValueError("Required fields missing")
     expected={k:vs for k,vs in g.items() if k in defs}
    offer={"offerId":s,"marketCategoryId":target,"parameterValues":flat}
    assert set(offer)=={"offerId","marketCategoryId","parameterValues"}
    rec={"kind":kind,"sku":s,"status":"submission_started","old_identity":row["identity"],"before":{"mapping":m,"card":c,"campaign":st},"payload":{"offerMappings":[{"offer":offer}]},"started_utc":now()}
    d["batches"].append(rec);row[kind+"_result"]={"status":"submission_started"};d["external_writes"]+=1;persist(d)
    resp=post(f"/v2/businesses/{biz}/offer-mappings/update",rec["payload"],retries=1)
    rec["response"]=resp;rec["status"]="submitted";persist(d)
    if any(x.get("errors") for x in resp.get("results",[]) if isinstance(x,dict)):raise ValueError("Market returned item errors")
    for attempt in range(5):
     if attempt:time.sleep(5)
     m2,c2,st2=live(biz,cam,s);after=protected(m2,c2,st2)
     diff=[k for k in pre if pre[k]!=after[k]]
     actual=groups(c2)
     expected_ok=all(sorted(actual.get(k,[]),key=canon)==sorted(vs,key=canon) for k,vs in expected.items())
     unexpected=[k for k in actual if k not in expected and any(v.get("value") or v.get("valueId") for v in actual[k])]
     # For an exact text OEM, tolerate server normalization of optional absent keys only.
     if kind=="attribute":
      change_ids={int(x["parameterId"]) for x in changes}
      expected_ok=all(existing_text(c2,int(x["parameterId"]))==[str(x["value"])] for x in changes) and grouped_norm(c2,change_ids)==grouped_norm(c,change_ids)
      unexpected=[]
     checks={"identity_preserved":pre["identity"]==after["identity"],"protected_diff":diff,"parameters_verified":expected_ok,"new_unexpected_parameters":unexpected,"category_verified":catid(m2,c2)==target,"card_status":c2.get("cardStatus"),"campaign_status":st2.get("status"),"errors":c2.get("errors",[])}
     rec["after"]={"mapping":m2,"card":c2,"campaign":st2};rec["checks"]=checks
     if not diff and expected_ok and not unexpected and checks["category_verified"]:break
    ok=not diff and expected_ok and not unexpected and checks["category_verified"] and c2.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE_ERRORS","NO_CARD_ERRORS","HAS_CARD_CAN_UPDATE_PROCESSING","NO_CARD_PROCESSING")
    rec["status"]="verified" if ok else "review";row[kind+"_result"]={"status":rec["status"],"checks":checks}
    if diff or unexpected:
     # Stop category transfers if actual identity or protected content drifts.
     stop_category=True
     if "identity" in diff or "offer" in diff or "commercial_status" in diff:
      d["status"]="blocked_protected_drift";persist(d);return
    persist(d)
   except Exception as e:
    row[kind+"_result"]={"status":"blocked_or_uncertain","reason":str(e)[:400]}
    save(d)
 d["status"]="processed_with_review"
 d["summary"]["applied_attributes"]=dict(collections.Counter(r.get("attribute_result",{}).get("status","not_applied") for r in d["registry"].values()))
 d["summary"]["applied_categories"]=dict(collections.Counter(r.get("category_result",{}).get("status","not_applied") for r in d["registry"].values()))
 d["finished_utc"]=now();persist(d);print(json.dumps(d["summary"],ensure_ascii=False))
if __name__=="__main__":
 if sys.argv[1]=="prepare":prepare()
 elif sys.argv[1]=="apply":apply()
 else:raise ValueError("Unknown mode")
