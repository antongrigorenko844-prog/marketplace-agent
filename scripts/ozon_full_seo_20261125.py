"""Owner-approved full Ozon SEO audit; exact identities; bounded changes only.
Never writes prices, stocks, descriptions, media, ads, other channels.
Category migrations use the original-card import path with preserved fields.
All plans and results are durable; uncertain submissions are not retried blindly."""
import sys,json,copy,re,unicodedata,datetime,time,collections,hashlib,subprocess
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
from content_manifest import check_ozon_description_write, normalize_url
from ozon_description_guard import projection, differences, canonical
from openpyxl import load_workbook
OUT=ROOT/"data/ozon_full_seo_20261125.json"
BASE=ROOT/"data/ozon_full_seo_baseline_20261125.json"
# Explicitly reviewed category candidates, not title regex mutations.
# Old type must match exactly; live tree + mandatory attribute schema revalidated.
PARTS=17028756; FILTERS=17028751; REPAIR=971100605; FILTER=970863591
GASKET=970984896; SEAL=98930; CONTROL=971005681
CATEGORY={
 "0am325025l":((PARTS,98840),(PARTS,REPAIR),"Толкатель вилки, не комплект сцепления"),
 "02E305051C":((PARTS,REPAIR),(FILTERS,FILTER),"Самостоятельный фильтр АКПП, не ремкомплект"),
 "0AM325433E":((PARTS,REPAIR),(FILTERS,FILTER),"Самостоятельный фильтр АКПП"),
 "filtr-dsg7-dq200":((PARTS,REPAIR),(FILTERS,FILTER),"Самостоятельный фильтр АКПП"),
 "filtr-dsg7-dq500":((PARTS,REPAIR),(FILTERS,FILTER),"Самостоятельный фильтр АКПП"),
 "0сц":((PARTS,REPAIR),(PARTS,SEAL),"Отдельный сальник КПП"),
 "prokladka-vtulki-niz-dq200":((PARTS,REPAIR),(PARTS,GASKET),"Отдельная прокладка втулки КПП"),
 "prokladka-vtulki-verh-dq200":((PARTS,REPAIR),(PARTS,GASKET),"Отдельная прокладка втулки КПП"),
 "prokladka-vtulki-dq200":((PARTS,REPAIR),(PARTS,GASKET),"Комплект прокладок втулки"),
 "Dq200dsg70am0cw":((PARTS,GASKET),(PARTS,REPAIR),"Комплект прокладок, сальников и пыльников"),
 "tolkateli-salniki-alum-komplekt":((PARTS,SEAL),(PARTS,REPAIR),"Смешанный ремонтный комплект, не только сальники"),
 **{sku:((PARTS,REPAIR),(PARTS,CONTROL),"Самостоятельный электронный блок управления мехатроником") for sku in ("0AM927769D","0AM927769E","0AM927769F","0AM927769G","0AM927769K","6t30-tcm-gen1","6t30-tcm-gen2")},
}
# OEM codes only for unambiguously identified actual part, never a compatibility code
OEM={
 **{s:s for s in ("0AM927769D","0AM927769E","0AM927769F","0AM927769G","0AM927769K","31705-X428B","317053TX0C","31705-1XF0C","0AM325433E","02E305051C","0AM325443D","0AM325219C","WHT001922","01X301127C","55565480","0AM325473","0AM325587E","0AM325413A","0AM325120A","0AM301212A","0AM325467J")},
 "0BH325183":"0BH325183B","0BH325183 B":"0BH325183B",
 "EA888":"06L115401S","Ea888gen3":"06L115678AD",
 "0am325091f":"0AM325091F","0am325091e":"0AM325091E",
}
# Explicit known copied OEM wrong ONLY for individual standalone control boards.
OEM_REPLACE={"0AM927769E":"0AM927769D","0AM927769F":"0AM927769D","0AM927769G":"0AM927769D","0AM927769K":"0AM927769D"}
LEGAL_HOLD={"EA888":"Корпус фильтра двигателя: ошибочный ТН ВЭД 8708409909; нужен подтверждённый код именно корпуса, не копировать код комплекта.",
 "Ea888gen3":"Клапан фильтра двигателя: ошибочный ТН ВЭД 8708409909; нужен подтверждённый код клапана.",
 "WHT001922":"Болт: тип автомобильный крепёж предпочтителен; проверить ТН ВЭД и обязательные поля без переноса кода другого товара.",
 "01X301127C":"Болт: тип автомобильный крепёж предпочтителен; проверить ТН ВЭД и обязательные поля.",
 "0am325025f":"Комплект болтов: проверить ТН ВЭД и обязательные поля крепежа.",
 "55565480":"Импульсное кольцо распредвала не является распредвалом; уточнить специальный тип и ТН ВЭД."}
SCHEMA={};TREE={}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d):
 d["updated_utc"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");tmp.replace(OUT)
def checkpoint(d):
 save(d)
 subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def exact_map(rows):
 result={}
 for x in rows:
  if not x.get("offer_id"):continue
  s=x["offer_id"]
  if s in result:raise ValueError("duplicate exact offer_id: "+s)
  result[s]=x
 return result
def ids(a):return int(a.get("id") or a.get("product_id") or 0)
def typ(a):return (int(a.get("description_category_id") or 0),int(a.get("type_id") or 0))
def attr(a,k):
 xs=[x for x in a.get("attributes",[]) if x.get("id")==k]
 if len(xs)>1:raise ValueError("duplicate attribute "+str(k))
 return xs[0] if xs else None
def values(a,k):
 return [str(x.get("value") or "").strip() for x in (attr(a,k) or {}).get("values",[]) if str(x.get("value") or "").strip()]
def canoncode(s):return re.sub(r"[\s\-\u2010-\u2015]+","",unicodedata.normalize("NFKC",str(s)).upper())
def containscode(title,code):return canoncode(code) in canoncode(title)
def workbook(path):
 ws=load_workbook(path,read_only=True,data_only=True).active
 rows=iter(ws.iter_rows(values_only=True));head=next(rows);out={}
 for r in rows:
  if not r[0]:continue
  s=str(r[0])
  if s in out:raise ValueError("duplicate exact master row: "+s)
  out[s]=dict(zip(head,r))
 return out
def tree():
 def visit(nodes,cat=None,path=()):
  for n in nodes:
   c=n.get("description_category_id") or cat
   nm=n.get("category_name") or n.get("type_name") or n.get("name") or ""
   p=path+(nm,)
   if c and n.get("type_id"):TREE[(int(c),int(n["type_id"]))]={"path":"/".join(p),"name":nm,"disabled":bool(n.get("disabled"))}
   visit(n.get("children") or n.get("types") or [],c,p)
 if not TREE:visit(oz.get_category_tree("RU"))
 return TREE
def schema(pair):
 if pair not in SCHEMA:
  xs=oz.get_category_attributes(*pair)
  if not xs:raise ValueError("schema unavailable "+str(pair))
  SCHEMA[pair]={int(x["id"]):x for x in xs if x.get("id")}
 return SCHEMA[pair]
def direct_read(sku,pid):
 rows=oz._post("/v4/product/info/attributes",{"filter":{"product_id":[pid]},"limit":1},retries=2).get("result",[])
 a=exact_map(rows).get(sku)
 if not a or ids(a)!=pid:raise ValueError("exact live SKU/product ID mismatch")
 return a
def one(fn,sku,pid=None):
 a=exact_map(fn([sku])).get(sku)
 if not a or (pid and ids(a)!=pid):raise ValueError("exact live SKU/info mismatch")
 return a
def attrs_payload(a):
 return {"id":int(a["id"]),"complex_id":a.get("complex_id",0),"values":[{k:v for k,v in x.items() if k in ("value","dictionary_value_id")} for x in a.get("values",[])]}
def normalized(a):
 return sorted([attrs_payload(x) for x in a.get("attributes",[])],key=canonical)
def make_attr(k,v):return {"id":k,"complex_id":0,"values":[{"dictionary_value_id":0,"value":str(v)}]}
def verified_free(defn,expected):
 return defn and defn.get("name")==expected and not defn.get("dictionary_id") and not defn.get("is_complex")
def valid_dict(pair,definition,v):
 if not definition.get("dictionary_id"):return True
 # Exact dictionary values only. No guessing IDs or copying unrelated values.
 if not v.get("dictionary_value_id") or not str(v.get("value") or "").strip():return False
 hits=oz.search_attribute_values(*pair,int(definition["id"]),str(v["value"]).split(" - ")[0],limit=50)
 return any(int(x.get("id") or 0)==int(v["dictionary_value_id"]) for x in hits)
def confirmed_description(master_row,cat_row):
 text=str(master_row.get("Описание") or "")
 return text if text and text==str(cat_row.get("Описание") or "") else None
def category_plan(a,sku,cat_row,master_row):
 if sku in LEGAL_HOLD:return {"status":"blocked_legal_or_type","reason":LEGAL_HOLD[sku]}
 plan=CATEGORY.get(sku)
 if not plan:return {"status":"no_confirmed_category_change","note":"Текущий тип проверен по дереву; близкие варианты без достаточного обоснования не меняются."}
 old,new,reason=plan
 if typ(a)==new:return {"status":"already_correct","reason":reason}
 if typ(a)!=old:return {"status":"blocked","reason":"текущий тип изменился с момента аудита"}
 if not master_row or not cat_row:return {"status":"blocked","reason":"нет точной записи в обоих каталогах"}
 if not tree().get(new) or tree()[new]["disabled"]:return {"status":"blocked","reason":"целевой тип отсутствует или отключён"}
 return {"status":"candidate","old":list(old),"new":list(new),"reason":reason,"destination":tree()[new]["path"]}
def attr_plan(a,sku,master_row,cat_row,defs):
 changes=[];notes=[]
 if not master_row or not cat_row:return changes,["Нет точной записи в обоих каталогах; автоматические правки запрещены."]
 title=str(a.get("name") or "")
 title1=str(master_row.get("Название товара") or "")
 title2=str(cat_row.get("Название товара") or "")
 if not title or not title1 or not title2:return changes,["Нет подтверждённого названия в обоих каталогах."]
 code=OEM.get(sku)
 if code:
  cur=values(a,7324)
  correct=len(cur)==1 and canoncode(cur[0])==canoncode(code)
  if not correct:
   if not all(containscode(t,code) for t in (title,title1,title2)):notes.append("OEM: код не подтверждён одновременно тремя названиями")
   elif not verified_free(defs.get(7324),"OEM-номер"):notes.append("OEM: поле отсутствует или изменилось в справочнике")
   elif not cur or (len(cur)==1 and sku in OEM_REPLACE and canoncode(cur[0])==OEM_REPLACE[sku]):
    changes.append({"attribute":make_attr(7324,code),"before":cur,"reason":"Точный номер самой детали подтверждён тремя названиями; не номер совместимой детали."})
   else:notes.append("OEM: иной уже заполненный номер, нужен отдельный разбор: "+str(cur))
 # Only copy explicitly approved composition from BOTH catalogs, never invent quantities.
 desc=confirmed_description(master_row,cat_row)
 if desc and not values(a,4384) and verified_free(defs.get(4384),"Комплектация"):
  m=re.search(r"(?:^|\n)\s*Комплектация\s*:\s*([^\n]+)",desc,re.I)
  if m:
   v=m.group(1).strip()
   if 0<len(v)<=500 and not re.search(r"уточнить|примерно|возможн",v,re.I):
    changes.append({"attribute":make_attr(4384,v),"before":[],"reason":"Комплектация дословно из совпадающих утверждённых описаний; физический состав не изменяется."})
 # Do not rewrite present tags. Existing exact master tags only; simple safety checks.
 tags=str(master_row.get("Хэштеги / Теги") or "").strip()
 ctags=str(cat_row.get("Хэштеги / ключевые слова (через запятую, пусто = не менять)") or "").strip()
 if not ctags:
  ctags=next((str(v or "").strip() for k,v in cat_row.items() if str(k).startswith("Хэштеги / ключевые слова")), "")
 if tags and ctags and not values(a,23171) and verified_free(defs.get(23171),"#Хештеги"):
  ts=[t for t in re.split(r"[\s,]+",tags) if t]
  cs=[t for t in re.split(r"[\s,]+",ctags) if t]
  if ts==cs and len(ts)<=30 and len(ts)==len(set(ts)) and all(re.fullmatch(r"#[\w]{1,29}",t,re.U) for t in ts):
   changes.append({"attribute":make_attr(23171," ".join(ts)),"before":[],"reason":"Точные совпадающие теги двух каталогов; без новых слов."})
 return changes,notes
def protected(a,pr,omit=(),category=False):
 p=projection(a,pr)
 # Existing description guard omits description; add it back here.
 p["description"]=attr(a,4191)
 p["attributes"]=[x for x in p["attributes"] if x.get("id") not in set(omit)|({8229} if category else set())]
 if category:
  p["fields"].pop("description_category_id",None);p["fields"].pop("type_id",None)
 return p
def clean_info(x):
 return {"product_id":ids(x),"sku":x.get("sku"),"model_info":x.get("model_info"),"statuses":x.get("statuses"),"errors":x.get("errors") or [],"archived":bool(x.get("is_archived")),"price":x.get("price"),"old_price":x.get("old_price"),"min_price":x.get("min_price"),"reviews":{k:v for k,v in x.items() if "review" in k.lower() or "rating" in k.lower()}}
def media(a):
 p=a.get("primary_image")
 if isinstance(p,list):
  if len(p)!=1:raise ValueError("ambiguous primary image")
  p=p[0]
 if isinstance(p,dict):p=p.get("file_name") or p.get("url")
 if not isinstance(p,str) or not p.startswith("https://"):raise ValueError("missing existing primary image")
 imgs=[]
 for item in a.get("images") or []:
  u=(item.get("file_name") or item.get("url")) if isinstance(item,dict) else item
  if not isinstance(u,str) or not u.startswith("https://"):raise ValueError("unreadable image URL")
  if u!=p and u not in imgs:imgs.append(u)
 return p,imgs
def price_payload(pr):
 p=pr.get("price") or {}
 if any(p.get(k) is None for k in ("price","old_price","vat")):raise ValueError("missing existing price/VAT")
 r={k:str(p[k]) for k in ("price","old_price","vat")}
 r["currency_code"]=p.get("currency_code") or pr.get("currency_code") or "RUB"
 if r["currency_code"]!="RUB" or Decimal(r["price"])<=0 or Decimal(r["old_price"])<0:raise ValueError("invalid preserved price")
 if Decimal(r["old_price"]) and Decimal(r["old_price"])<=Decimal(r["price"]):raise ValueError("inconsistent original old price")
 return r
def build_import(a,pr,plan):
 old,new=tuple(plan["old"]),tuple(plan["new"])
 if typ(a)!=old:raise ValueError("category drift")
 defs=schema(new); src=schema(old)
 orig={int(x["id"]):x for x in a.get("attributes",[]) if x.get("id")}
 required={k for k,v in defs.items() if v.get("is_required")}
 unknown=[(k,src.get(k,{}).get("name")) for k in orig if k not in defs and k!=8229]
 if unknown:raise ValueError("destination would lose existing attributes: "+str(unknown))
 missing=required-set(orig)-{8229}
 if missing:raise ValueError("mandatory destination values absent: "+str([(k,defs[k].get("name")) for k in missing]))
 if any(not values(a,k) for k in required&set(orig)-{8229}):raise ValueError("empty mandatory destination value")
 if a.get("complex_attributes"):raise ValueError("complex attributes require separate review")
 if not {85,22232,23536,4191}.issubset(orig):raise ValueError("brand/customs/marking/description unavailable")
 # Verify existing legally relevant values and every changed dictionary definition.
 for k,x in orig.items():
  if k==8229 or k not in defs:continue
  d=defs[k];old_d=src.get(k,{})
  if d.get("dictionary_id") and (k in {85,22232} or d.get("dictionary_id")!=old_d.get("dictionary_id")):
   if any(not valid_dict(new,d,v) for v in x.get("values",[])):raise ValueError("destination dictionary rejects preserved attribute "+str(k))
 attrs=[attrs_payload(v) for k,v in orig.items() if k!=8229]
 if 8229 in defs:
  name=tree()[new]["name"]
  opts=oz.search_attribute_values(*new,8229,name,limit=50)
  if not any(int(x.get("id") or 0)==new[1] and x.get("value")==name for x in opts):raise ValueError("destination type not confirmed in dictionary")
  attrs.append({"id":8229,"complex_id":0,"values":[{"dictionary_value_id":new[1],"value":name}]})
 dims={k:a.get(k) for k in ("depth","width","height","dimension_unit","weight","weight_unit")}
 if any(not dims[k] for k in dims) or any(Decimal(str(dims[k]))<=0 for k in ("depth","width","height","weight")):raise ValueError("dimensions not complete")
 p,imgs=media(a)
 if isinstance(a.get("barcode"),list) or len(a.get("barcodes") or [])>1:raise ValueError("multiple barcodes; no unsafe import")
 out={"offer_id":a["offer_id"],"name":a["name"],"description_category_id":old[0],"type_id":new[1],"attributes":attrs,"complex_attributes":a.get("complex_attributes") or [],"primary_image":p,"images":imgs,**dims,**price_payload(pr)}
 if old[0]!=new[0]:out["new_description_category_id"]=new[0]
 for k in ("barcode","color_image","pdf_list"):
  if k in a and a[k] is not None:out[k]=a[k]
 check_ozon_description_write("/v3/product/import",{"items":[out]})
 return out
def load_exact_catalog():
 catalog=exact_map(oz.list_products(limit=100))
 raw=exact_map(oz.get_product_names(limit=100))
 for sku,x in catalog.items():
  pid=ids(x)
  if not pid:continue
  if sku not in raw or ids(raw[sku])!=pid:
   try:raw[sku]=direct_read(sku,pid)
   except Exception:pass
 return catalog,raw
def prep():
 d={"version":1,"status":"planning","started_utc":now(),"external_writes":0,"registry":{},"batches":[],"errors":{}}
 save(d)
 master=workbook(ROOT/"data/master_control.xlsx");cat=workbook(ROOT/"data/ozon_catalog.xlsx")
 baseline=json.loads(BASE.read_text()) if BASE.exists() else {}
 report_skus=set(x["sku"] for x in baseline.get("rows",[]))
 tree()
 catalog,raw=load_exact_catalog()
 keys=sorted(set(catalog)|set(raw)|report_skus)
 try:info=exact_map(oz.get_product_info_list(list(set(catalog)|set(raw))))
 except Exception as e:info={};d["errors"]["info"]=str(e)[:300]
 try:pr=exact_map(oz.get_prices_and_stocks(list(set(catalog)|set(raw))))
 except Exception as e:pr={};d["errors"]["prices"]=str(e)[:300]
 try:stock=exact_map(oz.get_stocks(list(set(catalog)|set(raw))))
 except Exception as e:stock={};d["errors"]["stocks"]=str(e)[:300]
 d["counts"]={"list_products":len(catalog),"attributes":len(raw),"master":len(master),"ozon_catalog":len(cat),"report_unique_skus":len(report_skus)}
 rating={}
 skus=sorted({int(x["sku"]) for x in raw.values() if str(x.get("sku") or "").isdigit()})
 for i in range(0,len(skus),100):
  try:
   rr=oz._post("/v1/product/rating-by-sku",{"skus":skus[i:i+100]},retries=2)
   for x in rr.get("products") or []:rating[int(x["sku"])]=x.get("rating")
  except Exception as e:d["errors"]["rating_"+str(i)]=str(e)[:300]
 d["ratings_before"]=rating
 d["case_collisions"]={k:v for k,v in collections.defaultdict(list,((k,[x for x in keys if x.casefold()==k]) for k in {x.casefold() for x in keys})).items() if len(v)>1}
 for sku in keys:
  a=raw.get(sku); inf=info.get(sku); m=master.get(sku); c=cat.get(sku)
  row={"sku":sku,"in_master":bool(m),"in_ozon_catalog":bool(c),"in_report":sku in report_skus,"status":"checked","attribute_changes":[],"notes":[]}
  d["registry"][sku]=row
  if not a:
   row["status"]="blocked_identity";row["notes"].append("Нет точной карточки в Seller API; похожие SKU не объединяются.");continue
  row.update({"product_id":ids(a),"ozon_sku":a.get("sku"),"name":a.get("name"),"category":list(typ(a)),"category_path":tree().get(typ(a),{}).get("path"),"rating":rating.get(int(a.get("sku") or 0)),"info":clean_info(inf) if inf else None,"media_count":len(a.get("images") or [])+bool(a.get("primary_image"))})
  if sku=="TEST001" or (inf and inf.get("is_archived")):
   row["status"]="excluded_test_or_archive";continue
  if sku not in catalog or ids(catalog[sku])!=ids(a) or not inf or ids(inf)!=ids(a):
   row["status"]="blocked_identity";row["notes"].append("Не совпали точные ID списков/характеристик/информации.");continue
  if not tree().get(typ(a)):
   row["notes"].append("Тип не найден в текущем справочнике; требуется проверка.")
  try:
   defs=schema(typ(a))
   row["missing_required"]=[{"id":k,"name":v.get("name")} for k,v in defs.items() if v.get("is_required") and k!=8229 and not values(a,k)]
   fields=["OEM-номер","Альтернативные артикулы товара","Партномер (артикул производителя)","Вид техники","Комплектация","#Хештеги","Количество в упаковке, шт","Единиц в одном товаре","Материал","Расположение детали"]
   row["search_attributes"]={v["name"]:values(a,k) for k,v in defs.items() if v.get("name") in fields}
   row["category_plan"]=category_plan(a,sku,c,m)
   row["attribute_changes"],notes=attr_plan(a,sku,m,c,defs)
   row["notes"].extend(notes)
   if m and c:
    row["master_title"]=str(m.get("Название товара") or "")
    row["catalog_title"]=str(c.get("Название товара") or "")
    row["title_drift"]=row["name"]!=row["catalog_title"]
    if sku=="0am325025H" and "без платы" not in str(a.get("name","")).lower():row["notes"].append("Предложение: явно указать «без электронной платы» в названии. Название не изменено.")
   if inf.get("errors") or not pr.get(sku) or not stock.get(sku):
    row["status"]="blocked_live_state";row["notes"].append("Нет полного защищённого снимка или товар имеет ошибки.");continue
   # No mutation on unidentified model or any uncertain protected fields.
   protected(a,pr[sku])
   if row["category_plan"]["status"]=="candidate" or row["attribute_changes"]:row["status"]="planned"
   elif row["category_plan"]["status"].startswith("blocked") or row["missing_required"] or row["notes"]:row["status"]="review"
   else:row["status"]="no_confirmed_change"
  except Exception as e:row["status"]="blocked_preflight";row["notes"].append(str(e)[:350])
  save(d)
 d["status"]="planned";d["baseline"]=baseline.get("summary",{})
 d["schema_count"]=len(SCHEMA)
 checkpoint(d)
def read_live(row):
 s=row["sku"];p=row["product_id"]
 a=direct_read(s,p);i=one(oz.get_product_info_list,s,p);pr=one(oz.get_prices_and_stocks,s);st=one(oz.get_stocks,s)
 if i.get("is_archived") or i.get("errors"):raise ValueError("item archived or has live errors")
 if a.get("model_info")!=i.get("model_info") or a.get("sku")!=i.get("sku"):raise ValueError("model/SKU mismatch")
 return a,i,pr,st
def check_import(task,tries=6):
 last={}
 for _ in range(tries):
  last=oz.get_import_status(int(task))
  rows=(last.get("result") or {}).get("items") or last.get("items") or []
  if rows and all(str(r.get("status")) not in ("pending","processing","") for r in rows):return last
  time.sleep(5)
 return last
def check_row(row,before,after,changes=(),category=False):
 a,i,pr,st=before;b,j,p2,s2=after
 ignore=[c["attribute"]["id"] for c in changes]
 dif=differences(protected(a,pr,ignore,category),protected(b,p2,ignore,category))
 # differences() ignores unknown fields other than generic equality, includes description.
 check={"product_id_same":ids(a)==ids(b),"sku_same":a.get("sku")==b.get("sku"),"model_same":a.get("model_info")==b.get("model_info"),"protected_diff":dif,"stock_snapshot_same":st==s2,"moderation":j.get("statuses"),"errors":j.get("errors") or []}
 if changes:check["attributes_verified"]=all(values(b,c["attribute"]["id"])==[str(v["value"]).strip() for v in c["attribute"]["values"]] for c in changes)
 if category:check["category_verified"]=typ(b)==tuple(row["category_plan"]["new"])
 return check
def good(c):
 return c["product_id_same"] and c["sku_same"] and c["model_same"] and not c["protected_diff"] and not c["errors"] and c.get("attributes_verified",True) and c.get("category_verified",True)
def apply_attrs(d):
 # Separate one-item canary first, then bounded batches of <= 10.
 eligible=[r for r in d["registry"].values() if r["status"]=="planned" and r["attribute_changes"] and not r.get("attribute_result")]
 groups=([eligible[:1]] if eligible else [])+[eligible[i:i+10] for i in range(1,len(eligible),10)]
 stop=False
 for group in groups:
  if stop:
   for r in group:r["attribute_result"]={"status":"held_after_protected_drift"}
   continue
  before={};items=[]
  for row in group:
   s=row["sku"]
   try:
    live=read_live(row);a=live[0]
    if typ(a)!=tuple(row["category"]):raise ValueError("category drift before attributes")
    defs=schema(typ(a));wanted=[]
    for change in row["attribute_changes"]:
     k=change["attribute"]["id"];current=values(a,k);target=[str(v["value"]) for v in change["attribute"]["values"]]
     if current==target:continue
     if current!=change["before"]:raise ValueError("attribute drift before write")
     if k not in {7324,4384,23171} or not defs.get(k) or defs[k].get("dictionary_id"):raise ValueError("attribute outside verified write scope")
     wanted.append(change)
    if not wanted:row["attribute_result"]={"status":"already_correct"};continue
    before[s]=(live,wanted);items.append({"offer_id":s,"attributes":[x["attribute"] for x in wanted]})
   except Exception as e:row["attribute_result"]={"status":"blocked_preflight","reason":str(e)[:350]}
  if not items:save(d);continue
  rec={"kind":"attributes","items":[x["offer_id"] for x in items],"status":"sending","started":now(),"before":{s:{"card":v[0][0],"price":v[0][2],"stock":v[0][3],"info":clean_info(v[0][1])} for s,v in before.items()},"payload":items}
  d["batches"].append(rec);d["external_writes"]+=1
  for s in before:d["registry"][s]["attribute_result"]={"status":"submission_started"}
  checkpoint(d)
  try:
   resp=oz._post("/v1/product/attributes/update",{"items":items},retries=1)
   rec["response"]=resp;task=resp.get("task_id") or (resp.get("result") or {}).get("task_id")
   rec["task_id"]=task;rec["status"]="submitted";checkpoint(d)
   if task:rec["import_status"]=check_import(task)
   for s,(old,wanted) in before.items():
    r=d["registry"][s]
    try:
     fresh=read_live(r);ck=check_row(r,old,fresh,wanted)
     r["attribute_result"]={"status":"verified" if good(ck) else "review","task_id":task,"checks":ck,"count":len(wanted)}
     if ck["protected_diff"] or not ck["model_same"]:stop=True
    except Exception as e:r["attribute_result"]={"status":"unverified","reason":str(e)[:350],"task_id":task}
   rec["status"]="checked"
  except Exception as e:
   rec["status"]="uncertain";rec["error"]=str(e)[:350]
   for s in before:d["registry"][s]["attribute_result"]={"status":"uncertain_no_retry","reason":str(e)[:350]}
  checkpoint(d)
 return stop
def apply_categories(d,stop=False):
 for row in d["registry"].values():
  plan=row.get("category_plan") or {}
  if plan.get("status")!="candidate" or row.get("category_result"):continue
  if stop:
   row["category_result"]={"status":"held_after_protected_drift"};continue
  s=row["sku"]
  try:
   a,i,pr,st=live=read_live(row)
   if typ(a)==tuple(plan["new"]):row["category_result"]={"status":"already_correct"};continue
   if row["status"]!="planned":raise ValueError("initial plan blocked")
   if row.get("attribute_result",{}).get("status") not in (None,"already_correct","verified"):raise ValueError("unresolved attribute operation")
   p=build_import(a,pr,plan)
  except Exception as e:
   row["category_result"]={"status":"blocked_preflight","reason":str(e)[:400]};save(d);continue
  rec={"kind":"category","items":[s],"status":"sending","started":now(),"before":{"card":a,"price":pr,"stock":st,"info":clean_info(i)},"payload":p}
  d["batches"].append(rec);d["external_writes"]+=1;row["category_result"]={"status":"submission_started"}
  checkpoint(d)
  try:
   resp=oz.import_products([p],retries=1);rec["response"]=resp;task=(resp.get("result") or {}).get("task_id")
   rec["task_id"]=task;rec["status"]="submitted";checkpoint(d)
   if not task:raise ValueError("uncertain submission: no task_id")
   rec["import_status"]=check_import(task)
   fresh=read_live(row);ck=check_row(row,live,fresh,category=True)
   row["category_result"]={"status":"verified" if good(ck) else "review","task_id":task,"checks":ck}
   rec["status"]="checked"
   if ck["protected_diff"] or not ck["model_same"]:stop=True
  except Exception as e:
   rec["status"]="uncertain";rec["error"]=str(e)[:350]
   row["category_result"]={"status":"uncertain_no_retry","reason":str(e)[:350],"task_id":rec.get("task_id")}
  checkpoint(d)
def finalize(d):
 d["summary"]={"registry":len(d["registry"]),"initial_statuses":dict(collections.Counter(r["status"] for r in d["registry"].values())),
 "attribute_statuses":dict(collections.Counter(r.get("attribute_result",{}).get("status","not_applicable") for r in d["registry"].values())),
 "category_statuses":dict(collections.Counter(r.get("category_result",{}).get("status",r.get("category_plan",{}).get("status","not_applicable")) for r in d["registry"].values())),
 "attribute_fields_verified":sum(r.get("attribute_result",{}).get("count",0) for r in d["registry"].values() if r.get("attribute_result",{}).get("status")=="verified")}
 d["status"]="processed_with_review" if any(r["status"].startswith("blocked") or r["status"]=="review" or (r.get("category_result",{}).get("status") not in (None,"verified","already_correct")) or (r.get("attribute_result",{}).get("status") not in (None,"verified","already_correct")) for r in d["registry"].values()) else "verified"
 d["finished_utc"]=now();checkpoint(d);print(json.dumps(d["summary"],ensure_ascii=False))
def main():
 if sys.argv[1]=="prepare":
  if OUT.exists():
   d=json.loads(OUT.read_text())
   if d.get("external_writes"):print("Existing journal; no plan reset.");return
  prep()
 elif sys.argv[1]=="apply":
  d=json.loads(OUT.read_text())
  if d["status"] not in ("planned","processed_with_review","verified"):raise ValueError("plan not complete")
  if any(b["status"] in ("sending","submitted","uncertain") for b in d["batches"]):
   print("Uncertain previous submissions: never repeat them; continue only independent queued operations.")
  stop=apply_attrs(d)
  apply_categories(d,stop)
  finalize(d)
 else:raise ValueError("unknown mode")
if __name__=="__main__":main()
