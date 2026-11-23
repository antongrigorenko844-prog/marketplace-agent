"""LEOPARTS final all-SKU financial + BOM review, read-only.

Sources: live Avito API snapshot stored by 24-Nov audit; current master,
kits, Ozon/WB source catalogs; 36 legacy ads and 270d Avito statistics.
Avoids ever posting marketplace prices, mutating stock, feeds or master XLSX.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv, json, math, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse

D=ROOT/"data"
OUT=D/"leoparts_pricing_decision_20261124"
OUT.mkdir(parents=True,exist_ok=True)
def sheet(filename,name=None):
    pages=parse(D/filename)
    if name:
        return next((x["rows"] for x in pages if x["name"]==name),[])
    return pages[0]["rows"] if pages else []
def cell(row,i):
    return row[i] if i<len(row) else None
def key(x):
    return str(x).strip() if x is not None else ""
def num(x):
    try:
        if x in ("",None):return None
        v=float(str(x).replace(" ","").replace("\u00a0","").replace(",","."))
        return round(v,2) if math.isfinite(v) else None
    except (TypeError,ValueError):return None
def price(x): return num(x) if num(x) is not None and num(x)>0 else None
def format_ru(x):return f"{x:,.0f}".replace(","," ") if x is not None else "—"
def pct(a,b):
    return round(a/b*100,1) if b and a is not None else None
def dump_csv(name,headers,rows):
    with (OUT/name).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f,delimiter=";")
        w.writerow(headers)
        for r in rows:w.writerow(r)
def issue(severity,kind,sku,comment,**args):
    issues.append({"severity":severity,"kind":kind,"sku":sku,"message":comment,**args})

audit=json.loads((D/"avito_price_audit_20261124.json").read_text(encoding="utf-8"))
registry=json.loads((D/"avito_legacy_catalog_candidates_20261124.json").read_text(encoding="utf-8"))
stats=json.loads((D/"avito_legacy_selection_preflight_20261124.json").read_text(encoding="utf-8"))
index=json.loads((D/"avito_catalog_listing_index_20261124.json").read_text(encoding="utf-8"))
assert audit["counts"]["master_skus"]>=155
assert audit["mode"]=="read_only"
assert index["old_active_ads_grouped"]+index["old_active_ads_pending_review"]==36
assert audit["live_api_error"] is None and not audit["binding_errors"]

master={}
for row in sheet("master_control.xlsx","Товары")[1:]:
    sku=key(cell(row,0))
    if not sku:continue
    if sku in master:raise RuntimeError("Nonunique master SKU: "+sku)
    master[sku]={
        "sku":sku,"title":key(cell(row,5)) or key(cell(row,6)),
        "ozon_master":price(cell(row,7)),"wb_master":price(cell(row,10)),
        "cost":num(cell(row,26)),"stock":num(cell(row,9)),
        "notes":key(cell(row,13))[:150],
    }
assert len(master)==audit["counts"]["master_skus"],(len(master),audit["counts"]["master_skus"])
ozon={}
for row in sheet("ozon_catalog.xlsx","Ozon")[1:]:
    s=key(cell(row,0));v=price(cell(row,3))
    if s and v is not None:ozon[s]=v
wb={}
for row in sheet("wb_catalog.xlsx","WB")[1:]:
    s=key(cell(row,0));v=price(cell(row,5))
    if s and v is not None:wb[s]=v
by_sku={x["sku"]:x for x in audit["products"] if x["sku"] in master}
if len(by_sku)!=len(master):
    raise RuntimeError("Incomplete Avito product audit SKU coverage")
bom=defaultdict(Counter)
for row in sheet("kits.xlsx","Комплекты")[1:]:
    kit=key(cell(row,0));component=key(cell(row,2));qty=num(cell(row,3))
    if kit and component and qty and qty>0 and qty.is_integer():
        bom[kit][component]+=int(qty)
assert len(bom)==41,(len(bom),sorted(bom))
assert sum(map(len,bom.values()))==84
issues=[]
channels=("avito_main","site_config","ozon_source","wb_source")
prices={}
for sku,m in master.items():
    ap=by_sku[sku]
    mapped=ap.get("mapped") or {}
    prices[sku]={
        "avito_main":price(mapped.get("live_price")) if mapped.get("status")=="active" else None,
        "site_config":price(ap.get("site_cfg")),
        "ozon_source":ozon.get(sku) or m["ozon_master"],
        "wb_source":wb.get(sku) or m["wb_master"],
    }
    
bom_rows=[]
equal=defaultdict(list)
for kit,parts in bom.items():
    sig=tuple(sorted(parts.items()))
    equal[sig].append(kit)
    known_costs={k:master.get(k,{}).get("cost") for k in parts}
    has_all_costs=all(v is not None and v>=0 for v in known_costs.values())
    comp_cost=round(sum(known_costs[p]*q for p,q in parts.items()),2) if has_all_costs else None
    kcost=master.get(kit,{}).get("cost")
    diff=round(kcost-comp_cost,2) if kcost is not None and comp_cost is not None else None
    ava=prices.get(kit,{}).get("avito_main")
    selected_cost=max(kcost,comp_cost) if kcost is not None and comp_cost is not None else (kcost if kcost is not None else comp_cost)
    part_prices={}
    for ch in channels:
        t=[prices.get(s,{}).get(ch) for s in parts]
        part_prices[ch]=round(sum(prices[s][ch]*q for s,q in parts.items()),2) if all(v is not None for v in t) else None
        current=prices.get(kit,{}).get(ch)
        if current is not None and part_prices[ch] is not None and len(parts)>0:
            premium=round(current-part_prices[ch],2)
            if premium>1:
                severity="high" if ch=="avito_main" and premium>=100 else ("medium" if premium>=100 else "low")
                issue(severity,"kit_vs_individual_parts",kit,
                      "Набор дороже продажи точно указанных BOM-компонентов по отдельности",
                      channel=ch,kit_price=current,parts_price=part_prices[ch],premium=premium,
                      components=dict(parts))
    if diff is not None and abs(diff)>=1:
        if diff<0 and abs(diff)>=300:level="high"
        elif abs(diff)>=100:level="medium"
        else:level="low"
        issue(level,"kit_purchase_cost_difference",kit,
              "Проверьте разницу мастер-закупки и суммы закупки BOM. Разница может быть сборкой/упаковкой.",
              master_cost=kcost,component_cost=comp_cost,delta=diff,components=dict(parts))
    if kcost is None or not has_all_costs:
        issue("medium","kit_missing_cost",kit,
              "Невозможно проверить фактическую себестоимость комплекта/компонентов",
              master_cost=kcost,component_cost=comp_cost,missing=[k for k,v in known_costs.items() if v is None])
    avito_model=round(ava*0.89-selected_cost-50,2) if ava is not None and selected_cost is not None else None
    bom_rows.append({
        "sku":kit,"name":master.get(kit,{}).get("title") or "",
        "bom":dict(parts),"master_cost":kcost,"components_cost":comp_cost,
        "cost_diff":diff,"avito_price":ava,"parts_avito":part_prices["avito_main"],
        "site_price":prices.get(kit,{}).get("site_config"),
        "parts_site":part_prices["site_config"],
        "ozon_source":prices.get(kit,{}).get("ozon_source"),
        "parts_ozon":part_prices["ozon_source"],
        "wb_source":prices.get(kit,{}).get("wb_source"),
        "parts_wb":part_prices["wb_source"],
        "model_cost_floor":selected_cost,"hypothetical_avito_margin_before_views":avito_model,
        "cost_complete":has_all_costs and kcost is not None,
    })
# Detect two small kits with an identical BOM to a large one.
for big,B in bom.items():
    for small,S in bom.items():
        if big==small or len(B)!=len(S) or set(B)!=set(S):continue
        factors={B[p]/S[p] for p in B if S[p]>0}
        if len(factors)!=1:continue
        n=factors.pop()
        if not n.is_integer() or n<2:continue
        n=int(n)
        for ch in channels:
            a=prices.get(big,{}).get(ch);b=prices.get(small,{}).get(ch)
            if a is not None and b is not None and a>n*b+1:
                issue("high" if ch=="avito_main" else "medium",
                      "large_kit_more_than_smalls",big,
                      "Большой набор с тем же BOM дороже соответствующего числа малых наборов",
                      channel=ch,large=a,small_bundle=n*b,small_sku=small,small_qty=n,delta=a-n*b)
# Equal BOM, price differences across canonical SKUs, not by similar title.
for groups in equal.values():
    if len(groups)<2:continue
    for ch in channels:
        vals=[(s,prices.get(s,{}).get(ch)) for s in groups]
        vals=[(s,v) for s,v in vals if v is not None]
        if len(vals)<2:continue
        lo=min(vals,key=lambda x:x[1]);hi=max(vals,key=lambda x:x[1])
        if hi[1]>lo[1]+99:
            issue("high" if ch=="avito_main" else "medium",
                  "identical_bom_different_sku_prices",hi[0],
                  "Одинаковая запись BOM у разных SKU, разные цены; проверить материал/качество",
                  other_sku=lo[0],channel=ch,high=hi[1],low=lo[1],delta=hi[1]-lo[1])
# Price compliance and plausible margin.
rows=[]
for sku,m in master.items():
    ap=by_sku[sku];live=(ap.get("mapped") or {})
    cost=m["cost"]
    av=prices[sku]["avito_main"]
    cost_overrides=[r["components_cost"] for r in bom_rows
        if r["sku"]==sku and r["components_cost"] is not None]
    alt=max([cost]+cost_overrides) if cost is not None else (max(cost_overrides) if cost_overrides else None)
    hypothetical=round(av*.89-alt-50,2) if av is not None and alt is not None else None
    if av is not None and cost is None:
        issue("high" if av>=10000 else "medium","active_avito_missing_purchase_cost",
              sku,"Активная карточка Авито без закупочной стоимости — маржа неизвестна",live=av)
    if av is not None and hypothetical is not None and hypothetical<=0:
        issue("high","nonpositive_avito_scenario_margin",sku,
              "В сценарии 6% налог, 5% комиссия и 50 ₽ упаковка не покрыты",
              price=av,cost_floor=alt,margin=hypothetical)
    if av is not None and live.get("status")=="active" and ap.get("avito_cfg") is not None:
        if abs(av-ap["avito_cfg"])>=1:
            issue("high","active_vs_approved_price_mismatch",sku,
                  "Живая цена отличается от утверждённой цены фида",live=av,approved=ap["avito_cfg"])
    rows.append({
       "sku":sku,"name":m["title"],"cost_master":cost,"cost_floor":alt,
       "avito_main_id":live.get("id") if live.get("status")=="active" else None,
       "avito_live":av,"avito_approved":ap.get("avito_cfg"),
       "site_config":prices[sku]["site_config"],
       "ozon_source":prices[sku]["ozon_source"],"wb_source":prices[sku]["wb_source"],
       "markup_avito":round(av/cost,2) if cost and av is not None else None,
       "margin_avito_scenario":hypothetical,
       "legacy_secondary_ids":[v["avito_id"] for v in ap.get("legacy_secondary_catalog_refs",[])],
       "legacy_candidate_ids":[v["avito_id"] for v in ap.get("legacy_unverified_candidates",[])],
       "flags":[v["kind"] for v in issues if v["sku"]==sku],
    })
# Explicit existing incomplete/phantom non-master price overrides.
for x in audit["issues"]:
    if x["type"]=="orphan_override":
        issue("medium","orphan_price_override",x["sku"],
              "Цена набора есть в управляющем файле, но SKU нет в мастере и фиде",
              configured=x.get("configured"))
    if x["type"]=="case_sensitive_sku_collision":
        issue("high","case_sensitive_sku_names",x["sku"],
              "SKU отличаются регистром, но представляют разные товары; не объединять",variants=x.get("variants"))
legacy_out=[]
for e in stats["entries"]:
    candidates=e.get("candidate_skus",[])
    legacy_out.append({
       "old_id":e["old_avito_id"],"title":e["title"],"old_price":e["old_live_price"],
       "candidate_skus":[c["sku"] for c in candidates],
       "current_main_prices":[c["main_ad_price_live"] for c in candidates],
       "old_views":(e.get("old_269d_metrics") or {}).get("views"),
       "old_contacts":(e.get("old_269d_metrics") or {}).get("contacts"),
       "old_favorites":(e.get("old_269d_metrics") or {}).get("favorites"),
       "candidate_titles_verified":False,
       "status":"active_but_not_feed_managed",
       "action":"preserve_and_verify_composition",
    })
severity_order={"high":0,"medium":1,"low":2}
issues.sort(key=lambda q:(severity_order[q["severity"]],q["kind"],q["sku"]))
rows.sort(key=lambda x:x["sku"].casefold())
bom_rows.sort(key=lambda x:x["sku"].casefold())
legacy_out.sort(key=lambda x:x["old_id"])
# No automatic price changes: action plan is a financially reasoned queue.
recommendations=[]
def rec(sku,priority,action,why,price_target=None,channel=None):
    recommendations.append({"sku":sku,"priority":priority,"action":action,
                            "why":why,"proposed_price_rub":price_target,"channel":channel,
                            "approved_to_publish":False})
for b in bom_rows:
    if b["cost_diff"] is not None and b["cost_diff"]< -100:
        rec(b["sku"],"P1","verify_master_cost",
            f"Мастер {format_ru(b['master_cost'])} ₽; компоненты {format_ru(b['components_cost'])} ₽. Проверить актуальные закупки и дополнительные затраты.")
for x in issues:
    if x["kind"]=="kit_vs_individual_parts" and x.get("channel")=="avito_main" and x.get("premium",0)>=100:
        rec(x["sku"],"P0","review_kit_price_bom",
            f"Авито {format_ru(x['kit_price'])} ₽ за набор > {format_ru(x['parts_price'])} ₽ за компоненты; надо подтвердить BOM, не менять цену автоматически.")
    if x["kind"]=="identical_bom_different_sku_prices" and x.get("channel")=="avito_main":
        rec(x["sku"],"P0","verify_equal_bom_quality",
            f"С одинаковой структурой BOM продаётся другой SKU {x['other_sku']} за {format_ru(x['low'])} ₽.")
for sku in ("nabor-chip-0am927769d","nabor-chip-02e927770"):
    rec(sku,"P1","remove_or_reconcile_orphan_override",
        "Отсутствует в мастере и действующем фиде; 2 000 ₽ премии над единственным BOM-компонентом пока не доказывают неправильную живую цену.")
rec("4285108577 / 4220729305","P0","compare_actual_contents",
    "Название одинаково, но 990 ₽ может быть набором сальник+пыльник+крышка. Не объединять с 5 890 ₽.")
for sku in ("02E 305 045","DQ500"):
    rec(sku,"CLOSED","keep_approved_avito_1390",
        "Живая цена и утверждённый фид 1 390 ₽; сайт 1 480 ₽ оставлен сознательно.",1390,"avito")
# Mandatory review lines for all flagged missing cost.
for row in rows:
    if row["avito_live"] is not None and row["cost_master"] is None:
        rec(row["sku"],"P1","enter_real_purchase_cost",
            "Активный товар без закупки. Не пересчитывать цену до определения себестоимости.")
report={
 "generated_utc":datetime.now(timezone.utc).isoformat(),
 "mode":"read_only_owner_decision_draft","seller_writes":0,"stock_writes":0,
 "feed_writes":0,"master_price_writes":0,"pricing_formula_enabled":False,
 "sources":{
    "avito_live":"data/avito_price_audit_20261124.json",
    "avito_live_captured_utc":audit["generated_utc"],
    "legacy_stats":"data/avito_legacy_selection_preflight_20261124.json",
    "master":"data/master_control.xlsx","bom":"data/kits.xlsx",
    "ozon":"data/ozon_catalog.xlsx (source catalog, NOT live API)",
    "wb":"data/wb_catalog.xlsx (source catalog, NOT live API)",
    "site":"configured site price, NOT independently fetched storefront",
 },
 "fee_scenario":{"tax":0.06,"avito_delivery":0.05,"packaging":50,
                 "warning":"Hypothetical 5% delivery fee/50 RUB packaging; paid views, refunds and delivery extras excluded."},
 "counts":{
    "master_skus":len(master),"active_avito_main":sum(x["avito_live"] is not None for x in rows),
    "active_legacy_ads":len(legacy_out),"bom_kits":len(bom),"bom_relations":sum(map(len,bom.values())),
    "bom_cost_comparable":sum(b["cost_complete"] for b in bom_rows),
    "bom_cost_different":sum(b["cost_diff"] is not None and abs(b["cost_diff"])>=1 for b in bom_rows),
    "bom_cost_understated":sum(b["cost_diff"] is not None and b["cost_diff"]<-1 for b in bom_rows),
    "avito_active_no_master_cost":sum(x["avito_live"] is not None and x["cost_master"] is None for x in rows),
    "price_actions_already_completed":2,
    "new_price_actions_executed":0,
    "issues":len(issues),
    "high":sum(x["severity"]=="high" for x in issues),
    "medium":sum(x["severity"]=="medium" for x in issues),
    "low":sum(x["severity"]=="low" for x in issues),
    "action_proposals":len(recommendations),
 },
 "issues":issues,"recommendations":recommendations,
 "all_bom":bom_rows,"all_products":rows,"all_legacy":legacy_out,
}
(OUT/"decision.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
dump_csv("all_159_skus.csv",
 ["SKU","Товар","Закупка мастер ₽","Риск-пол себестоимости ₽","ID основной Avito",
  "Цена Avito API ₽","Цена Avito одобрена ₽","Цена сайта (конфиг) ₽","Ozon каталог ₽",
  "WB каталог ₽","Цена / закупка Avito","Вклад до платных просмотров (сценарий) ₽",
  "Старые Avito ID","Неопределённые кандидаты ID","Флаги"],
 [(x["sku"],x["name"],x["cost_master"],x["cost_floor"],x["avito_main_id"],
   x["avito_live"],x["avito_approved"],x["site_config"],x["ozon_source"],
   x["wb_source"],x["markup_avito"],x["margin_avito_scenario"],
   ",".join(map(str,x["legacy_secondary_ids"])),
   ",".join(map(str,x["legacy_candidate_ids"])),
   ",".join(x["flags"])) for x in rows])
dump_csv("all_41_kits.csv",
 ["SKU набора","Название","BOM JSON","Закупка мастера","Закупка компонентов","Разница",
  "Цена Авито","Компоненты Авито","Цена сайта конфиг","Компоненты сайта",
  "Цена Ozon каталог","Компоненты Ozon","Цена WB каталог","Компоненты WB",
  "Сценарный вклад Авито до просмотров"],
 [(b["sku"],b["name"],json.dumps(b["bom"],ensure_ascii=False),b["master_cost"],
   b["components_cost"],b["cost_diff"],b["avito_price"],b["parts_avito"],
   b["site_price"],b["parts_site"],b["ozon_source"],b["parts_ozon"],
   b["wb_source"],b["parts_wb"],b["hypothetical_avito_margin_before_views"])
   for b in bom_rows])
dump_csv("all_36_legacy_ads.csv",
 ["Avito ID","Название","Живая цена ₽","Кандидаты SKU","Цены основных ₽",
  "Просмотры 270 дней","Контакты 270 дней","Избранное 270 дней","Действие"],
 [(x["old_id"],x["title"],x["old_price"],",".join(x["candidate_skus"]),
   ",".join(str(y or "") for y in x["current_main_prices"]),x["old_views"],
   x["old_contacts"],x["old_favorites"],x["action"]) for x in legacy_out])
dump_csv("findings_and_proposals.csv",
 ["Приоритет","Тип","SKU","Проблема","Источник/подробности","Автоматическая публикация"],
 [(q["severity"],q["kind"],q["sku"],q["message"],
   json.dumps({k:v for k,v in q.items() if k not in ("severity","kind","sku","message")},
              ensure_ascii=False),False)
  for q in issues])
def mdline(s):return str(s).replace("|","/").replace("\n"," ")
md=[
 "# LEOPARTS — финансовый аудит всей матрицы",
 "",
 f"Сформировано UTC: {report['generated_utc']}. Выполнено в режиме только чтения. ",
 "## Полнота",
 f"- Уникальных SKU: {len(master)}, активных основных Avito: {report['counts']['active_avito_main']}, старых активных: {len(legacy_out)}.",
 f"- BOM: {len(bom)} комплектов и 84 связи. Сравнимых закупок: {report['counts']['bom_cost_comparable']}, разных: {report['counts']['bom_cost_different']}, с заниженной мастер-закупкой: {report['counts']['bom_cost_understated']}.",
 f"- Цена с отсутствующей мастер-закупкой среди активных Avito: {report['counts']['avito_active_no_master_cost']}.",
 f"- Флаги по всему расчёту: {len(issues)}. Не приравнивать число флагов к числу ошибочных цен.",
 "",
 "## Главные решения",
 "- Два комплекта DQ250/DQ500: утвержденные 1 390 ₽ на Avito уже применены; сайт 1 480 ₽ не трогать.",
 "- Сверить 41 BOM по оригинальности/материалам/реальной закупке: налог и платные просмотры не включены в себестоимость.",
 "- Объявления за 990 ₽ и 5 890 ₽ — только одинаковые заголовки, реальный состав не подтвержден. Не объединять.",
 "- Корпуса DQ500 разных типов не объединять без фото/артикула.",
 "- Старыe объявления оставить активными до сравнения состава и показателей; подмена AvitoId может повлиять на фид.",
 "- Две записи nabor-chip-* есть в ценовых оверрайдах, но не в мастере/фиде; не считать их активными наборами.",
 "- Низкая закупка отдельной мелочи не требует снижения цены: важны реклама, упаковка и возвраты.",
 "",
 "## Точные расхождения себестоимости BOM (фактическая мастер-таблица)",
 "| SKU | Закупка мастера | Сумма компонентов | Разница |",
 "|---|---:|---:|---:|"
]
for b in sorted([z for z in bom_rows if z["cost_diff"] is not None and abs(z["cost_diff"])>=1],
                key=lambda x:(x["cost_diff"],x["sku"])):
    md.append(f"| {mdline(b['sku'])} | {format_ru(b['master_cost'])} ₽ | {format_ru(b['components_cost'])} ₽ | {format_ru(b['cost_diff'])} ₽ |")
md+=["","## Значимые инверсии комплектов относительно деталей"]
bad=[q for q in issues if q["kind"] in
     ("kit_vs_individual_parts","large_kit_more_than_smalls","identical_bom_different_sku_prices")]
if not bad:md.append("Проверенных по текущей матрице инверсий нет.")
else:
    for q in bad:
        if len(md)>185:break
        md.append("- "+mdline(q["sku"])+": "+mdline(q["kind"])+
                  ", "+mdline(q.get("channel"))+", "+mdline(q))
md+=["","## Недостающая закупка для активных объявлений"]
for z in rows:
    if z["avito_live"] is not None and z["cost_master"] is None:
        md.append("- "+z["sku"]+": Avito "+format_ru(z["avito_live"])+" ₽; без закупки прибыль не подтверждается.")
md+=["","## Финансовый сценарий, не факт прибыли",
"Для Авито при известной закупке рассчитано: цена × 0,89 − max(мастер-закупка, сумма BOM) − 50 ₽.",
"0,89 отражает гипотезы 6% УСН и 5% комиссии. Не учтены платные просмотры, возвраты, иные сборы.",
"**Этот показатель нельзя назвать проверенной чистой прибылью.**",
"","## Полный набор результатов",
"- all_159_skus.csv — каждая позиция, цена, закупка, маржа и старые ID.",
"- all_41_kits.csv — детальный BOM и проверка по Ozon/WB, сайту и Авито.",
"- all_36_legacy_ads.csv — старые объявления и статистика за 270 дней.",
"- findings_and_proposals.csv — полный журнал флагов.",
"- decision.json — структурированный исходный результат с рекомендациями.",
"","## Ограничения",
"- Живые цены Авито: дата сохраненного API-снимка "+audit["generated_utc"]+".",
"- Ozon/WB и сайт в этом отчете — цены каталога и конфигурации, а не проверенная цена покупки в реальном времени.",
"- Фактический состав старых объявлений не доступен из подключенного API. Результат «похоже по названию» — не факт идентичности.",
"- Изменений цен, остатков, карточек и автозагрузки этот аудит не выполняет.",
]
(OUT/"REPORT_RU.md").write_text("\n".join(md)+"\n",encoding="utf-8")
print("LEOPARTS_DECISION_AUDIT_OK",json.dumps(report["counts"],ensure_ascii=False),flush=True)
print("TOP_RISKS",json.dumps(issues[:18],ensure_ascii=False)[:5600],flush=True)
