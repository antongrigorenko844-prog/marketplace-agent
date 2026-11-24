"""Read-only, Market-specific financial and publication decision report.
Uses current Partner API tariff amounts and exact-case SKU matches to master cost.
Never sets prices, stocks, groups, promotions, documents, or other marketplaces.
"""
import sys,json,datetime,collections,math,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import market_full_seo_20261125 as s
import yandex_market_client as ym
OUT=ROOT/"data/market_growth_decision_20261125.json"
BASE=ROOT/"data/market_full_seo_baseline_20261125.json"
REC=ROOT/"data/market_growth_recovery_20261125.json"
TAX=.06
CORE={"FEE","PAYMENT_TRANSFER","AGENCY_COMMISSION","DELIVERY_TO_CUSTOMER","EXPRESS_DELIVERY_TO_CUSTOMER","MIDDLE_MILE","FULFILLMENT","SORTING"}
REQUIRED={"FEE","PAYMENT_TRANSFER","DELIVERY_TO_CUSTOMER","MIDDLE_MILE"}
def num(x):
 if isinstance(x,bool):return None
 try:
  y=float(x)
  return y if math.isfinite(y) else None
 except (ValueError,TypeError):return None
def save(x):OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+"\n")
def get_pages(method,path,key,body=None,limit=200):
 out=[];token=None;seen=set()
 for _ in range(20):
  p={"limit":limit}
  if token:p["pageToken"]=token
  kw={"params":p,"retries":1}
  if method=="POST":kw["json_body"]=body or {}
  code,r=ym._request(method,path,**kw)
  if code!=200 or r.get("status")!="OK":raise ValueError(f"HTTP {code}: {ym._errors_text(r)[:200]}")
  result=r.get("result") or {}
  if not isinstance(result.get(key),list):raise ValueError("Expected response list "+key+"; got "+str(list(result)))
  out.extend(result[key])
  token=(result.get("paging") or {}).get("nextPageToken")
  if not token:return out
  if token in seen:raise ValueError("Repeated page token")
  seen.add(token)
 raise ValueError("Incomplete pagination")
def fee_projection(t,price,current_price):
 if price==current_price:return num(t.get("amount"))
 pp={p["name"]:p.get("value") for p in t.get("parameters",[]) if p.get("name")}
 # A threshold-dependent tariff can't be projected safely outside its active range.
 low=num(pp.get("priceFrom"));high=num(pp.get("priceTo"))
 if low is not None and price<low or high is not None and price>=high:return None
 pct=num(t.get("percent"))
 if pct is None:
  if pp.get("valueType")!="absolute":return None
  return num(t.get("amount"))
 if not 0<=pct<=100:return None
 fee=price*pct/100
 lo=num(pp.get("minValue"));hi=num(pp.get("maxValue"))
 if lo is not None:fee=max(lo,fee)
 if hi is not None:fee=min(hi,fee)
 return fee
def margin_at(price,original,cost,tariffs):
 amounts=[fee_projection(t,price,original) for t in tariffs]
 if price<=0 or any(x is None for x in amounts):return None
 expenses=sum(amounts);contribution=price-cost-expenses-price*TAX
 return {"price":round(price,2),"known_fees":round(expenses,2),"tax_scenario":round(price*TAX,2),"contribution_before_ads_returns_overhead":round(contribution,2),"margin_pct":round(100*contribution/price,2)}
def available(r):
 vals=[num(x.get("count")) for w in r.get("warehouses",[]) for x in w.get("stocks",[]) if x.get("type")=="AVAILABLE"]
 return sum(vals) if vals and all(x is not None for x in vals) else None
def run():
 biz,cam=s.account()
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":cam,"read_only":True,"external_writes":0,"checked_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"errors":{},"rows":{}}
 save(d)
 master=s.wsrows(ROOT/"data/master_control.xlsx")
 base=s.index(json.loads(BASE.read_text())["rows"],lambda r:r["sku"])
 m=s.index(s.get_mappings(biz),lambda r:r["offer"]["offerId"])
 cards=s.index(s.batched_read(biz,"offer-cards",list(m)),lambda x:x["offerId"])
 status=s.campaign_status(cam,list(m))
 if set(m)!=set(cards) or set(m)!=set(status):raise ValueError("Incomplete live catalog")
 stats=[]
 for i in range(0,len(m),100):
  try:
   r=s.post(f"/v2/campaigns/{cam}/stats/skus",{"shopSkus":list(m)[i:i+100]})
   stats+=r["result"]["shopSkus"]
  except Exception as e:d["errors"]["tariffs_"+str(i)]=str(e)[:300]
 stats=s.index(stats,lambda r:r.get("shopSku"))
 try:
  recommendations=get_pages("POST",f"/v2/businesses/{biz}/offers/recommendations","offerRecommendations",{"offerIds":list(m)})
  recommends=s.index(recommendations,lambda x:x.get("offer",{}).get("offerId"))
 except Exception as e:recommends={};d["errors"]["recommendations"]=str(e)[:300]
 # Check hidden-offers and both quarantine levels without modifying any prices.
 for key,path in (("business_quarantine",f"/v2/businesses/{biz}/price-quarantine"),("campaign_quarantine",f"/v2/campaigns/{cam}/price-quarantine")):
  try:
   result=s.post(path,{},params={"limit":200})
   body=result.get("result",{})
   hits=body.get("quarantineOffers",body.get("offers"))
   if not isinstance(hits,list):raise ValueError("Unknown quarantine result shape "+str(list(body)))
   if (body.get("paging") or {}).get("nextPageToken"):raise ValueError("Incomplete quarantine result")
   d[key]=[{"offerId":x.get("offerId"),"verdicts":x.get("verdicts"),"currentPrice":x.get("currentPrice"),"lastValidPrice":x.get("lastValidPrice")} for x in hits]
  except Exception as e:d["errors"][key]=str(e)[:300]
 try:d["manually_hidden"]=[x["offerId"] for x in get_pages("GET",f"/v2/campaigns/{cam}/hidden-offers","hiddenOffers",limit=500)]
 except Exception as e:d["errors"]["manually_hidden"]=str(e)[:300]
 # Inspect only document attachment and validation status, never create a document
 # or infer an obligation from absence of a certificate.
 try:
  docs=get_pages("POST",f"/v1/businesses/{biz}/offers/documents","documents",{},limit=100)
  bynum={str(x.get("number")):x for x in docs if x.get("number")}
  d["document_diagnostics"]={"registered_count":len(docs),"registered_statuses":dict(collections.Counter(x.get("status") for x in docs)),"blocked_offers":{}}
  for sku,x in m.items():
   if status[sku].get("status")!="DISABLED_AUTOMATICALLY":continue
   o=x["offer"];certs=o.get("certificates") or []
   d["document_diagnostics"]["blocked_offers"][sku]={"attached_count":len(certs),"attached_document_statuses":[bynum.get(str(z),{}).get("status","not_in_registry") for z in certs],"commodity_codes":o.get("commodityCodes"),"condition":o.get("condition"),"marking_declared":o.get("markingDeclared"),"media_statuses":{k:[v.get("uploadState") for v in vs if isinstance(v,dict)] for k,vs in (o.get("mediaFiles") or {}).items() if isinstance(vs,list)}}
 except Exception as e:d["errors"]["document_diagnostics"]=str(e)[:300]
 d["current_counts"]=dict(collections.Counter(x.get("status") for x in status.values()))
 d["coverage"]={"catalog":len(m),"cards":len(cards),"tariffs":len(stats),"price_recommendations":len(recommends)}
 for sku,x in m.items():
  c=cards[sku];st=status[sku];r=stats.get(sku,{})
  o=x["offer"];source=base.get(sku)
  row={"sku":sku,"name":o.get("name"),"identity":s.identity(x,c),"model":x.get("mapping",{}).get("marketModelId"),"category":s.catid(x,c),"category_name":x.get("mapping",{}).get("marketCategoryName"),"campaign_status":st.get("status"),"card_status":c.get("cardStatus"),"rating":c.get("contentRating"),"average_category_rating":c.get("averageContentRating"),"recommendations":c.get("recommendations",[]),"ugc":o.get("ugc"),"errors":st.get("errors",[])+c.get("errors",[]),"warnings":st.get("warnings",[])+c.get("warnings",[]),"stock":available(r),"master_stock":master.get(sku,{}).get("Остаток, шт."),"baseline":source}
  row["params"]=c.get("parameterValues",[])
  rr=recommends.get(sku,{})
  offer_rec=rr.get("offer") or {};thresholds=(rr.get("recommendation") or {}).get("competitivenessThresholds") or {}
  row["price_competition"]={"rating":offer_rec.get("competitiveness"),"shows_7d":offer_rec.get("shows"),"catalog_price":offer_rec.get("price"),"optimal":thresholds.get("optimalPrice"),"average":thresholds.get("averagePrice")}
  price=num((st.get("basicPrice") or {}).get("value"));statsprice=num(r.get("price"));cost=num(master.get(sku,{}).get("Закупка, ₽"))
  raw=[t for t in r.get("tariffs",[]) if t.get("type") in CORE]
  row["price"]=price;row["purchase_cost"]=cost;row["fee_percent"]=next((t.get("percent") for t in raw if t.get("type")=="FEE"),None)
  if price and statsprice==price and cost is not None and cost>0 and REQUIRED<={t.get("type") for t in raw} and all(t.get("currency")=="RUR" for t in raw):
   row["current_economics"]=margin_at(price,price,cost,raw)
   for label,k in (("optimal_economics","optimalPrice"),("average_economics","averagePrice")):
    val=thresholds.get(k) or {}
    p=num(val.get("value"))
    if p and val.get("currencyId")=="RUR":row[label]=margin_at(p,price,cost,raw)
  d["rows"][sku]=row
 save(d)
 # Reconcile every previously submitted correction against current state; never resubmit.
 old=json.loads((ROOT/"data/market_full_seo_20261125.json").read_text())
 recovery=json.loads(REC.read_text())
 d["verified_previous"]={}
 for b in old.get("batches",[]):
  sku=b["sku"]
  if sku not in m:continue
  r=d["rows"][sku]
  wanted=b["payload"]["offerMappings"][0]["offer"]
  if b["kind"]=="category":verified=r["category"]==wanted["marketCategoryId"]
  else:
   target=[v.get("value") for v in wanted.get("parameterValues",[]) if v["parameterId"]==60134505]
   verified=s.existing_text(cards[sku],60134505)==target
  d["verified_previous"][sku]={"kind":b["kind"],"value_matches":verified,"moderation":r["card_status"],"campaign_status":r["campaign_status"],"category":r["category"]}
 d["verified_types"]={}
 for op in recovery.get("operations",[]):
  sku=op.get("sku")
  if sku not in m:continue
  want=next((p for p in op["payload"]["offersContent"][0]["parameterValues"] if p["parameterId"]==21194330),None)
  if not want:continue
  vals=s.groups(cards[sku]).get(21194330,[])
  good=len(vals)==1 and (vals[0].get("valueId")==want.get("valueId") if want.get("valueId") else str(vals[0].get("value","")).casefold()==str(want["value"]).casefold())
  pre=op["before_protected"];now=s.protected(m[sku],cards[sku],status[sku])|{"model":m[sku].get("mapping",{}).get("marketModelId")}
  diff=[k for k in pre if pre[k]!=now.get(k)]
  # Compare protected fields as well as non-target typed parameters.
  prev={k:sorted(v,key=s.canon) for k,v in s.groups(op["before"]).items() if k!=21194330}
  other={k:sorted(v,key=s.canon) for k,v in s.groups(cards[sku]).items() if k!=21194330}
  d["verified_types"][sku]={"value_matches":good,"moderation":cards[sku].get("cardStatus"),"protected_diff":diff,"other_attributes_preserved":prev==other,"campaign_status":status[sku].get("status"),"value":vals}
 rows=list(d["rows"].values())
 eco=[r for r in rows if r.get("current_economics")]
 loss=[r for r in eco if r["current_economics"]["contribution_before_ads_returns_overhead"]<=0]
 low=[r for r in eco if 0<r["current_economics"]["margin_pct"]<10]
 active=[r for r in rows if r["campaign_status"]=="PUBLISHED"]
 d["summary"]={"cost_and_tariff_coverage":len(eco),"missing_cost":sum(r["purchase_cost"] is None or r["purchase_cost"]<=0 for r in rows),"loss_or_zero":len(loss),"low_margin_under_10_pct":len(low),"price_competitiveness":dict(collections.Counter(r["price_competition"]["rating"] or "NOT_AVAILABLE" for r in rows)),"published_price_competitiveness":dict(collections.Counter(r["price_competition"]["rating"] or "NOT_AVAILABLE" for r in active)),"blocked_with_actual_stock":sum(r["campaign_status"]=="DISABLED_AUTOMATICALLY" and (r["stock"] or 0)>0 for r in rows),"blocked_actual_units":sum(r["stock"] or 0 for r in rows if r["campaign_status"]=="DISABLED_AUTOMATICALLY"),"fee_rates":dict(collections.Counter(str(r["fee_percent"]) for r in rows))}
 d["notes"]=["Contribution is a scenario at seller price, with current modeled direct fulfillment tariffs, purchase cost and assumed USN 6%; excludes ads, returns, overhead and other actual accounting adjustments.","Price recommendation is Market's threshold, not an independently sampled competitor price, and not an authorization to change prices.","Source workbook has unknown period; recommendations.shows is rolling 7 days and not comparable to unknown-period workbook.","No product changes were made by this script. Missing values remain null."]
 d["finished_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
 save(d)
 print(json.dumps({"coverage":d["coverage"],"summary":d["summary"],"errors":d["errors"]},ensure_ascii=False))
if __name__=="__main__":run()
