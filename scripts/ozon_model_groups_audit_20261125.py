"""Ozon LEOPARTS: read-only audit of actual model membership, stored labels and aspects.
No Ozon product, price, stock, review or group mutation.
"""
import collections,json,sys,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
from ozon_full_seo_20261125 import exact_map,ids,typ,values,schema,tree
OUT=ROOT/"data/ozon_model_groups_audit_20261125.json"
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d):
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
def run():
 d={"platform":"Ozon","mode":"read_only","external_writes":0,"started_utc":now(),"errors":{},"rows":{}}
 cat=exact_map(oz.list_products(limit=100))
 info=exact_map(oz.get_product_info_list(list(cat)))
 attrs=exact_map(oz.get_product_names(limit=100))
 # Repair only missing READ coverage by known numeric ID.
 for sku,x in cat.items():
  if sku not in attrs or ids(attrs[sku])!=ids(x):
   try:
    a=exact_map(oz._post("/v4/product/info/attributes",{"filter":{"product_id":[ids(x)]},"limit":1},retries=1).get("result",[])).get(sku)
    if a and ids(a)==ids(x):attrs[sku]=a
   except Exception as e:d["errors"][sku]=str(e)[:250]
 d["coverage"]={"listed":len(cat),"info":len(info),"attributes":len(attrs),"exact_both":sum(k in attrs and k in info and ids(attrs[k])==ids(x)==ids(info[k]) for k,x in cat.items())}
 d["missing"]=sorted(k for k,x in cat.items() if k not in attrs or k not in info or ids(attrs[k])!=ids(x) or ids(info[k])!=ids(x))
 try:paths=tree()
 except Exception as e:paths={};d["errors"]["tree"]=str(e)[:200]
 for sku,x in cat.items():
  a=attrs.get(sku,{});i=info.get(sku,{})
  if ids(a)!=ids(x) or ids(i)!=ids(x):continue
  pair=typ(a);m=i.get("model_info") or {};am=a.get("model_info") or {}
  if am and m!=am:d["errors"]["model:"+sku]={"info":m,"attributes":am}
  r={"sku":sku,"product_id":ids(x),"ozon_sku":str(i.get("sku") or a.get("sku") or ""),"name":a.get("name"),"model_id":str(m.get("model_id") or ""),"model_count":m.get("count"),"archived":bool(i.get("is_archived")),"status":(i.get("statuses") or {}).get("status_name"),"moderation":(i.get("statuses") or {}).get("moderate_status"),"category":list(pair),"category_path":paths.get(pair,{}).get("path"),"price":i.get("price"),"group_label":values(a,9048),"attributes":{},"review_metadata":{k:v for k,v in i.items() if "review" in k.lower() or "rating" in k.lower()}}
  try:
   defs=schema(pair)
   for k,v in defs.items():
    name=v.get("name") or ""
    if v.get("is_aspect") or k==9048 or name in ("Бренд","Название модели (для объединения в одну карточку)","OEM-номер","Альтернативные артикулы товара","Партномер (артикул производителя)","Комплектация","Единиц в одном товаре","Количество в упаковке, шт","Материал","Размер","Цвет товара","Расположение детали"):
     r["attributes"][str(k)]={"name":name,"aspect":v.get("is_aspect"),"required":v.get("is_required"),"values":values(a,k)}
  except Exception as e:d["errors"]["schema:"+sku]=str(e)[:200]
  d["rows"][sku]=r
 groups=collections.defaultdict(list);labels=collections.defaultdict(list);unknown=[]
 for k,x in d["rows"].items():
  if x["model_id"] and x["model_id"]!="0":groups[x["model_id"]].append(k)
  else:unknown.append(k)
  for label in x["group_label"]:
   if label.strip():labels[label.strip()].append(k)
 d["model_groups"]={k:{"members":v,"reported_counts":sorted(set(d["rows"][s]["model_count"] for s in v if isinstance(d["rows"][s]["model_count"],int))),"categories":[list(x) for x in sorted(set(tuple(d["rows"][s]["category"]) for s in v))]} for k,v in groups.items() if len(v)>1 or any((d["rows"][s]["model_count"] or 0)>1 for s in v)}
 d["labels_not_real_groups"]={k:{"members":v,"actual_models":sorted(set(d["rows"][s]["model_id"] for s in v))} for k,v in labels.items() if len(v)>1 and len(set(d["rows"][s]["model_id"] for s in v))>1}
 d["model_id_unknown"]=unknown
 d["summary"]={"actual_models":len(groups),"multi_model_groups":len(d["model_groups"]),"members_in_multi_groups":sum(len(x["members"]) for x in d["model_groups"].values()),"singleton_models":sum(len(v)==1 and (d["rows"][v[0]]["model_count"] or 0)==1 for v in groups.values()),"unknown_model_id":len(unknown),"archived":sum(x["archived"] for x in d["rows"].values()),"same_label_different_models":len(d["labels_not_real_groups"])}
 d["finished_utc"]=now();save(d)
 print(json.dumps({"coverage":d["coverage"],"summary":d["summary"],"groups":d["model_groups"],"unknown":unknown,"errors":d["errors"]},ensure_ascii=False))
if __name__=="__main__":run()
