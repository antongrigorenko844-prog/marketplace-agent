"""LEOPARTS Avito price audit. READ ONLY for seller and existing catalogs.

Audits master, individual Avito feed, Avito/site overrides, kit BOM and actual
seller API prices without changing seller data. Report only sanitized business
attributes; never persist credentials, private addresses or personal data.
"""
from __future__ import annotations
from collections import defaultdict, Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import json, math, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client

REQ=ROOT/"data/avito_price_audit_request_20261124.json"
OUT=ROOT/"data/avito_price_audit_20261124.json"
TAX=0.06
AVITO_FEE_SCENARIO=0.05
PACKING_SCENARIO=50
CPV_SCENARIO=6.90

def rows(path,sheet=None):
    book=parse(str(ROOT/path))
    if sheet:
        book=[s for s in book if s["name"]==sheet]
    return book[0]["rows"] if book else []

def value(row,col,default=None):
    return row[col] if len(row)>col and row[col] is not None else default

def number(val):
    try:
        if val is None or val=="":return None
        x=float(str(val).strip().replace(" ","").replace(",","."))
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):
        return None

def rounded(x):
    return round(x,2) if x is not None else None

def inventory():
    m={}
    for r in rows("data/master_control.xlsx","Товары")[1:]:
        sku=str(value(r,0,"")).strip()
        if not sku:continue
        if sku in m:raise ValueError("Duplicate SKU in master "+sku)
        m[sku]={"title":str(value(r,5,"")).strip(),"cost":number(value(r,26)),
                "ozon_master":number(value(r,7)),"stock_master":number(value(r,9)),
                "avito_flag":value(r,18),"master_note":str(value(r,13,""))[:180]}
    oz={}
    for r in rows("data/ozon_catalog.xlsx","Ozon")[1:]:
        sku=str(value(r,0,"")).strip()
        if sku:oz[sku]={"ozon_catalog":number(value(r,3)),"stock_ozon_catalog":number(value(r,7))}
    cfg={}
    for r in rows("data/avito_site_price_overrides.xlsx","Цены Avito и сайт")[1:]:
        sku=str(value(r,0,"")).strip()
        if not sku:continue
        if sku in cfg:raise ValueError("Duplicate SKU override "+sku)
        price=number(value(r,1))
        site=number(value(r,3))
        cfg[sku]={"avito_cfg":price,"site_cfg":site if site is not None else price,
                  "site_override_explicit":site is not None,"fixed":str(value(r,2,"")).strip().lower()=="да"}
    feed={}
    for r in rows("data/avito_feed.xlsx","Объявления")[4:]:
        sku=str(value(r,3,"")).strip()
        if not sku:continue
        if sku in feed:raise ValueError("Duplicate Avito feed Id "+sku)
        feed[sku]={"feed_price":number(value(r,33)),
                   "feed_title":str(value(r,23,""))[:120],
                   "bind_avito_id":number(value(r,8)),
                   "fee_method":str(value(r,6,""))[:50],
                   "ad_category":str(value(r,39,""))[:75]}
    # Product-specific website prices take precedence over the default
    # site=Avito fallback, e.g. verified electronic control boards.
    site_overrides=json.loads((ROOT/"data/site_price_only_overrides.json").read_text(encoding="utf-8"))
    for sku,site in site_overrides.items():
        if sku in cfg:cfg[sku]["site_cfg"]=number(site)
    # Five approved Nov-23 products do not exist in legacy override workbook.
    # They are intentionally approved via the immutable intake manifest.
    intake=json.loads((ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json").read_text(encoding="utf-8"))
    assert intake["approved"] is True
    intake_added=0
    for item in intake["items"]:
        sku=item["sku"]
        if sku not in cfg:
            intake_added+=1
            cfg[sku]={"avito_cfg":number(item["prices"]["avito"]),
                      "site_cfg":number(item["prices"]["tilda"]),
                      "fixed":True,"price_source":"approved_intake_manifest"}
    bom=defaultdict(lambda:defaultdict(int))
    for r in rows("data/kits.xlsx","Комплекты")[1:]:
        kit=str(value(r,0,"")).strip()
        component=str(value(r,2,"")).strip()
        qty=number(value(r,3))
        if kit and component and qty and qty>0 and qty.is_integer():
            bom[kit][component]+=int(qty)
    return m,oz,cfg,feed,{k:dict(v) for k,v in bom.items()},intake_added

def live_data(feed,additional):
    response={"error":None,"listings":[],"bindings":{},"bindings_errors":[]}
    try:
        l=avito_client.list_all_items(per_page=100,max_pages=12)
        response["listings"]=[{"id":x.get("id"),"title":str(x.get("title") or "")[:120],
                               "status":x.get("status"),"price":number(x.get("price")),
                               "url":x.get("url")}
                             for x in l if x.get("id") is not None]
    except Exception as ex:response["error"]=type(ex).__name__+": "+str(ex)[:200]
    skus=list(dict.fromkeys(list(feed)+list(additional)))
    for index in range(0,len(skus),30):
        chunk=skus[index:index+30]
        try:
            res=avito_client._request("GET","/autoload/v2/items/avito_ids",
                  params={"query":",".join(chunk)},retries=1)
            for ent in res.get("items") or []:
                sku=str(ent.get("ad_id") or "")
                aid=ent.get("avito_id")
                if sku in skus and aid is not None:
                    response["bindings"].setdefault(sku,[]).append(aid)
        except Exception as ex:response["bindings_errors"].append({"offset":index,"error":str(ex)[:150]})
    return response

def analyze():
    req=json.loads(REQ.read_text(encoding="utf-8"))
    assert req.get("mode")=="read_only" and req.get("change_prices") is False
    master,oz,cfg,feed,bom,intake_added=inventory()
    source_feed_rows=len(feed)
    links=json.loads((ROOT/"data/avito_links.json").read_text(encoding="utf-8"))
    # '-av' is an explicit linkage suffix for 8 existing ads, not a second SKU.
    # Critical: preserve case-sensitive identities, DQ500 != Dq500.
    aliases={}
    for raw in list(feed):
        cleaned=raw.replace("\u2011","-")
        match_link=next((sku for sku in links
                         if cleaned==sku.replace("\u2011","-")+"-av"),None)
        if match_link is not None:
            # Historical 123014-AF has a nonbreaking-hyphen spelling in master
            # but ASCII spelling in Avito link JSON; this is ONE product.
            matched=next((sku for sku in master
                          if sku.replace("\u2011","-")==match_link),match_link)
            aliases[raw]=matched
            if matched not in feed:
                feed[matched]=dict(feed.pop(raw),source_feed_id=raw)
    seller=live_data(feed,list(master)+list(cfg)+list(aliases))
    by_id={str(x["id"]):x for x in seller["listings"]}
    resolved={}
    rowset=[]
    for sku in dict.fromkeys(list(master)+list(cfg)+list(feed)):
        mm=master.get(sku,{})
        cc=cfg.get(sku,{})
        ff=feed.get(sku,{})
        candidates=list(seller["bindings"].get(sku,[]))
        raw=ff.get("source_feed_id")
        if raw:candidates+=seller["bindings"].get(raw,[])
        link_key=next((k for k in links
                       if k.replace("\u2011","-")==sku.replace("\u2011","-")),None)
        if link_key is not None:candidates.append(links[link_key])
        fb=ff.get("bind_avito_id")
        if fb and fb not in candidates:candidates.append(int(fb))
        matches=[by_id[str(n)] for n in candidates if str(n) in by_id]
        match_ids=list(dict.fromkeys(str(x["id"]) for x in matches))
        if len(match_ids)==1:
            item=by_id[match_ids[0]]
            resolved[sku]={"id":item["id"],"status":item.get("status"),"live_price":item.get("price"),
                           "url":item.get("url"),"live_title":item.get("title"),"source":"exact_avito_id"}
        elif len(match_ids)>1:
            resolved[sku]={"source":"ambiguous_multiple_ids","ids":match_ids}
        else:
            resolved[sku]={"source":"not_mapped","ids":list(dict.fromkeys(map(str,candidates)))}
        r={"sku":sku, "name":mm.get("title") or ff.get("feed_title") or "",
           "cost_master":mm.get("cost"),
           "avito_cfg":cc.get("avito_cfg"),"site_cfg":cc.get("site_cfg"),
           "fixed":cc.get("fixed"),"price_source":cc.get("price_source") or "avito_site_overrides",
           "source_feed_id":ff.get("source_feed_id") or sku,
           "price_in_feed":ff.get("feed_price"),
           "ozon_catalog":oz.get(sku,{}).get("ozon_catalog") or mm.get("ozon_master"),
           "stock_catalog":oz.get(sku,{}).get("stock_ozon_catalog"),
           "in_feed":sku in feed,"in_master":sku in master,"avito_flag":mm.get("avito_flag"),
           "mapped":resolved[sku]}
        ref_price=(resolved[sku].get("live_price") if resolved[sku].get("status")=="active"
                    else None)
        if ref_price is None: ref_price=ff.get("feed_price") or cc.get("avito_cfg")
        r["model_price_source"]="active_live" if resolved[sku].get("status")=="active" else ("feed" if ff.get("feed_price") is not None else "override")
        r["model_price"]=ref_price
        if ref_price and mm.get("cost") and mm["cost"]>0:
            r["markup_to_cost"]=rounded(ref_price/mm["cost"])
            contribution=ref_price*(1-TAX-AVITO_FEE_SCENARIO)-mm["cost"]-PACKING_SCENARIO
            r["contribution_before_view_cost"]=rounded(contribution)
            r["breakeven_paid_views"]=math.floor(contribution/CPV_SCENARIO) if contribution>0 else 0
        else:
            r["markup_to_cost"]=r["contribution_before_view_cost"]=r["breakeven_paid_views"]=None
        rowset.append(r)
    issues=[]
    def add(priority,kind,sku,detail,**data):
        issues.append({"priority":priority,"type":kind,"sku":sku,"detail":detail,**data})
    for r in rowset:
        sku=r["sku"]
        p=r["price_in_feed"];c=r["avito_cfg"];s=r["site_cfg"]
        actual=r["mapped"].get("live_price")
        if p is not None and c is not None and abs(p-c)>.01:
            add("high","feed_vs_approved_override",sku,
                "Цена в опубликованном XLSX-фиде отличается от действующего источника цены Avito",feed=p,configured=c,delta=rounded(p-c))
        if r["mapped"].get("status")=="active" and p is not None and actual is not None and abs(actual-p)>.01:
            add("high","active_vs_feed",sku,
                "Реальная активная цена Avito не совпадает с опубликованным фидом",live=actual,feed=p,delta=rounded(actual-p),url=r["mapped"].get("url"))
        if r["mapped"].get("status")=="active" and c is not None and actual is not None and abs(actual-c)>.01:
            add("high","active_vs_override",sku,
                "Цена активного объявления не совпадает с утверждённым управляющим файлом",live=actual,configured=c,delta=rounded(actual-c),url=r["mapped"].get("url"))
        if p is not None and c is None:
            add("medium","feed_without_approved_price",sku,"Публикуется в фиде, но нет цены в ручных оверрайдах или утверждённой заявке",feed=p)
        if c is not None and sku not in master and sku not in feed:
            add("low","orphan_override",sku,"Есть цена-оверрайд, но SKU отсутствует в мастере и в фиде",configured=c)
        if r["mapped"].get("source")=="ambiguous_multiple_ids":
            add("high","ambiguous_avito_binding",sku,"Один SKU связывается с несколькими объявлениями",ids=r["mapped"].get("ids"))
        if r.get("markup_to_cost") is not None:
            if r["markup_to_cost"]<2.5:
                add("medium","low_markup",sku,"Невысокая розничная кратность к закупке; проверить гарантию и логистику",price=r["model_price"],cost=r["cost_master"],multiple=r["markup_to_cost"])
            if r["contribution_before_view_cost"]<=0:
                add("high","negative_pretraffic_contribution",sku,"Нет положительного вклада даже до расходов на просмотры",price=r["model_price"],cost=r["cost_master"],profit=r["contribution_before_view_cost"])
            if r["markup_to_cost"]>20 and r["cost_master"]<=200:
                add("low","extreme_markup_or_cost_stale",sku,"Очень высокая кратность для дешёвой детали; проверить цену и закупку",price=r["model_price"],cost=r["cost_master"],multiple=r["markup_to_cost"])
        if p is not None and s is not None and s>p:
            add("medium","site_more_expensive_than_avito",sku,"Сайт дороже Avito для этой позиции",feed=p,site=s,delta=rounded(s-p))
        if p is not None and r["ozon_catalog"] and p>r["ozon_catalog"]+.01:
            add("medium","avito_more_expensive_than_ozon",sku,"Авито дороже Ozon (проверить комплектацию/скидки)",avito=p,ozon=r["ozon_catalog"])
    direct={r["sku"]:r for r in rowset}
    for kit,components in bom.items():
        if kit not in direct:continue
        k=direct[kit]
        for label in ("price_in_feed","avito_cfg","site_cfg"):
            if not k.get(label):continue
            if any(comp not in direct or not direct[comp].get(label) for comp in components):continue
            part_sum=sum(direct[comp][label]*qty for comp,qty in components.items())
            premium=rounded(k[label]-part_sum)
            if premium>0:
                add("high" if premium>=100 else "medium","kit_more_expensive_than_parts",kit,
                    "Набор стоит дороже того же BOM по отдельности",channel=label,
                    kit=rounded(k[label]),parts=rounded(part_sum),extra=premium,components=components)
            elif part_sum>0 and k[label]>=part_sum*.98:
                add("low","kit_nearly_no_discount",kit,
                    "У комплекта скидка к самостоятельной сборке меньше 2%",channel=label,
                    kit=rounded(k[label]),parts=rounded(part_sum),discount_pct=rounded(100*(part_sum-k[label])/part_sum))
        if k.get("cost_master") is not None and all(comp in direct and direct[comp].get("cost_master") is not None for comp in components):
            total=sum(direct[comp]["cost_master"]*qty for comp,qty in components.items())
            delta=rounded(k["cost_master"]-total)
            if abs(delta)>max(100,total*.10):
                add("high" if abs(delta)>=500 else "medium","bom_purchase_cost_mismatch",kit,
                    "Себестоимость комплекта в мастере не совпадает с суммой компонентов",master_cost=k["cost_master"],bom_cost=rounded(total),delta=delta,components=components)
    for big,bom_big in bom.items():
        if big not in direct:continue
        for small,bom_small in bom.items():
            if big==small or small not in direct:continue
            coeffs=[]
            for q,qty in bom_big.items():
                if q not in bom_small or bom_small[q]<=0:break
                coeffs.append(qty/bom_small[q])
            else:
                if len(bom_big)!=len(bom_small) or set(bom_big)!=set(bom_small):continue
                if coeffs and len(set(coeffs))==1 and coeffs[0]>=2 and coeffs[0].is_integer():
                    n=int(coeffs[0])
                    for ch in ("price_in_feed","avito_cfg","site_cfg"):
                        bp=direct[big].get(ch); sp=direct[small].get(ch)
                        if bp is not None and sp is not None and bp>n*sp:
                            add("high","large_kit_more_expensive_than_smalls",big,
                                "Большой набор дороже полностью эквивалентных малых",channel=ch,
                                large=bp,alternative=rounded(n*sp),extra=rounded(bp-n*sp),qty=n,small_sku=small)
    casefold=defaultdict(list)
    for sku in direct:casefold[sku.casefold()].append(sku)
    for low,skus in casefold.items():
        if len(skus)>1:
            add("high","case_sensitive_sku_collision"," / ".join(skus),
                "SKU отличаются только регистром; нельзя объединять в одно предложение или прайс",variants=skus)
    active=[x for x in seller["listings"] if x["status"]=="active"]
    names=defaultdict(list)
    for x in active:names[x["title"].lower()].append(x)
    for same,items in names.items():
        if len(items)>1:
            add("medium","same_live_title_multiple_ads","", "Несколько активных объявлений имеют совпадающий заголовок",
                ads=[{"id":x["id"],"price":x["price"],"title":x["title"]} for x in items])
    # Same-title rod kits at 990 and 5890 are NOT confirmed identical.
    # Owner suspects the 990 offer may contain only seal + boot + rod cap.
    # Do not recommend price parity or automatic SKU merge before checking BOM.
    suspected_ids=(4285108577,4220729305)
    original_pair=[x for x in active if x.get("id") in suspected_ids]
    if len(original_pair)==2 and len({x.get("price") for x in original_pair})>1:
        add("medium","same_title_rod_kits_need_composition_review",
            "4285108577 / 4220729305",
            "Одинаковые заголовки, разные цены, состав НЕ подтверждён; 990 ₽ может быть малым набором",
            ads=[{"id":x.get("id"),"title":x.get("title"),
                  "price":x.get("price")} for x in original_pair],
            price_gap=round(abs(original_pair[0]["price"]-original_pair[1]["price"]),2),
            owner_confirmed_identical_components=False)
    costs=[r for r in rowset if r["in_master"] and r["cost_master"] and r["model_price"]]
    counts=Counter(x["priority"] for x in issues)
    return {"generated_utc":datetime.now(timezone.utc).isoformat(),
        "mode":"read_only","seller_writes":0,"catalog_writes":0,
        "assumptions":{"tax_rate":TAX,"avito_delivery_fee_scenario":AVITO_FEE_SCENARIO,
                      "packaging_scenario_rub":PACKING_SCENARIO,"cost_per_view_scenario_rub":CPV_SCENARIO,
                      "warning":"These are scenario allocations, not the actual commission or views for each SKU."},
        "counts":{"master_skus":len(master),"approved_price_sources_skus":len(cfg),
                  "override_workbook_skus":len(cfg)-intake_added,"feed_skus":source_feed_rows,
                  "known_alias_feed_rows":len(aliases),
                  "bom_kits":len(bom),"bom_relations":sum(len(a) for a in bom.values()),
                  "api_listed_ads":len(seller["listings"]),
                  "active_ads":len(active),"matched_by_id":sum(x["source"]=="exact_avito_id" for x in resolved.values()),
                  "matched_active":sum(x.get("status")=="active" for x in resolved.values()),
                  "with_cost_and_price":len(costs),"issues":len(issues),"priority":dict(counts)},
        "live_api_error":seller["error"],"binding_errors":seller["bindings_errors"],
        "issues":issues,"products":rowset,
        "unmapped_live_ads":[{"id":x["id"],"title":x["title"],"price":x["price"],"status":x["status"]}
           for x in seller["listings"] if str(x["id"]) not in
              {str(e.get("id")) for e in resolved.values() if e.get("id") is not None}],
        "feed_titles":{k:feed[k]["feed_title"] for k in feed},
        "source":{"master":"data/master_control.xlsx","overrides":"data/avito_site_price_overrides.xlsx",
                  "feed":"data/avito_feed.xlsx","kit_bom":"data/kits.xlsx",
                  "ozon_catalog":"data/ozon_catalog.xlsx",
                  "seller_api":"GET /core/v1/items and GET /autoload/v2/items/avito_ids"},
    }

def main():
    report=analyze()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("AVITO_FULL_AUDIT",json.dumps({"counts":report["counts"],
      "live_api_error":report["live_api_error"],"binding_errors":report["binding_errors"],
      "top_issues":report["issues"][:8]},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
