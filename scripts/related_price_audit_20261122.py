"""Read-only comparison of all linked kit/component prices across LEOPARTS sales channels."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json, math
from related_price_inspect_20261122 import parse

D=Path("data")
OUT=D/"related_price_audit_20261122"
OUT.mkdir(exist_ok=True)
def sheet(path,name=None):
    books=parse(str(D/path))
    return next((s["rows"] for s in books if s["name"]==name),[]) if name else books[0]["rows"]
def cell(r,i):
    return r[i] if len(r)>i else None
def money(v):
    try:
        x=float(str(v).replace(" ","").replace("\u00a0","").replace(",","."))
        return round(x,2) if math.isfinite(x) and x>0 else None
    except (ValueError,TypeError): return None
def text(v): return str(v).strip() if v is not None else ""

m={}
duplicates=Counter()
for r in sheet("master_control.xlsx","Товары")[1:]:
    k=text(cell(r,0))
    if not k:continue
    duplicates[k]+=1
    m[k]={"name":text(cell(r,5)) or text(cell(r,6)), "ozon":money(cell(r,7)),
          "wb":money(cell(r,10)),"cost":money(cell(r,26))}
for sku,n in duplicates.items():
    if n>1:m.pop(sku,None)

all_ch=("ozon","wb","market_intended","avito_config","site_config","avito_feed","avito_live")
prices={ch:{} for ch in all_ch}
for k,v in m.items():
    for ch,field in (("ozon","ozon"),("wb","wb"),("market_intended","ozon"),
                     ("avito_config","ozon"),("site_config","ozon")):
        if v[field]: prices[ch][k]=v[field]
overrides={}
for r in sheet("avito_site_price_overrides.xlsx")[1:]:
    k=text(cell(r,0))
    if not k:continue
    a=money(cell(r,1));s=money(cell(r,3)) or a
    overrides[k]={"avito":a,"site":s,"fixed":text(cell(r,2)).lower() in ("да","yes","1","фикс")}
    if a: prices["avito_config"][k]=a
    if s: prices["site_config"][k]=s

for r in sheet("ozon_catalog.xlsx","Ozon")[1:]:
    k=text(cell(r,0));p=money(cell(r,3))
    if k and p:prices["ozon"][k]=p
for r in sheet("wb_catalog.xlsx","WB")[1:]:
    k=text(cell(r,0));p=money(cell(r,5))
    if k and p:prices["wb"][k]=p

avrows=sheet("avito_feed.xlsx","Объявления")
h=next((r for r in avrows[:6] if "Уникальный идентификатор объявления" in r),None)
if h and "Цена" in h:
    i,j=h.index("Уникальный идентификатор объявления"),h.index("Цена")
    for r in avrows[5:]:
        k=text(cell(r,i));p=money(cell(r,j))
        if k and p:prices["avito_feed"][k]=p

ava=json.loads((D/"avito_autoload.json").read_text(encoding="utf-8"))
live=json.loads((D/"avito_live.json").read_text(encoding="utf-8"))
idx={str(v.get("avito_id")):text(v.get("ad_id")) for v in ava.get("items",[]) if v.get("avito_id") and v.get("ad_id")}
for item in live.get("items",[]):
    k=idx.get(str(item.get("id")));p=money(item.get("price"))
    if k and p and item.get("status")=="active": prices["avito_live"][k]=p
site_snap=json.loads((D/"site_prices.json").read_text(encoding="utf-8"))

bom=defaultdict(Counter);bom_name={}
for r in sheet("kits.xlsx","Комплекты")[1:]:
    k=text(cell(r,0));p=text(cell(r,2));q=money(cell(r,3))
    if not k or not p or not q or not float(q).is_integer():continue
    bom[k][p]+=int(q)
    bom_name[k]=text(cell(r,1))
bom=dict(bom)

changed=set(json.loads((D/"approved_market_prices_20261121.json").read_text(encoding="utf-8")).get("prices",{}))
changed|={r[0] for r in json.loads((D/"approved_direct_prices_20261121.json").read_text(encoding="utf-8")) if r and isinstance(r[0],str)}

issues=[];comparison=[];coverage=Counter();missing=[];quality=[]
def issue(kind,ch,k,left,right,parts=None,other=None,comment=""):
    if left is None or right is None: return
    issues.append({"kind":kind,"channel":ch,"sku":k,"title":m.get(k,{}).get("name") or bom_name.get(k),
        "price":left,"comparison":round(right,2),"delta":round(left-right,2),
        "parts":parts or [],"other":other,"comment":comment,
        "fixed":bool(overrides.get(k,{}).get("fixed")) if "avito" in ch or "site" in ch else False,
        "recently_changed_related":bool(({k}|set(bom.get(k,{})))&changed)})
for k,parts in bom.items():
    if k not in m:quality.append({"type":"kit_not_in_master","sku":k})
    for component,q in parts.items():
        if component not in m:quality.append({"type":"component_not_in_master","sku":k,"part":component})
    for ch in all_ch:
        kp=prices[ch].get(k)
        details=[{"sku":p,"qty":q,"unit":prices[ch].get(p),
                  "subtotal":round(prices[ch][p]*q,2) if prices[ch].get(p) else None}
                 for p,q in parts.items()]
        not_found=[v["sku"] for v in details if v["unit"] is None]
        if not_found:
            missing.append({"sku":k,"channel":ch,"missing_parts":not_found})
            continue
        if kp is None:continue
        coverage[ch]+=1
        total=round(sum(x["subtotal"] for x in details),2)
        comparison.append({"sku":k,"channel":ch,"kit":kp,"parts_sum":total,"delta":round(kp-total,2)})
        if len(parts)==1 and next(iter(parts.values()))==1:continue
        if kp>total+0.5:
            issue("kit_more_than_parts",ch,k,kp,total,details,comment="Набор дороже тех же деталей по отдельности")
        elif abs(kp-total)<=0.5:
            issue("kit_no_discount",ch,k,kp,total,details,comment="Нет экономии за комплект")

# Compare full pack to exactly two smaller packs; do not conflate non-original and original.
for ch in all_ch:
    for big,B in bom.items():
        bp=prices[ch].get(big)
        if bp is None:continue
        for small,S in bom.items():
            sp=prices[ch].get(small)
            if small==big or sp is None:continue
            if Counter({k:2*q for k,q in S.items()})==B:
                if bp>=sp*2-0.5:
                    issue("two_small_kits_cheaper",ch,big,bp,sp*2,[{"sku":small,"qty":2,"unit":sp}],
                          other=small,comment="Состав совпадает с двумя малыми наборами")
            if S!=B and all(B.get(k,0)>=q for k,q in S.items()):
                if bp+10<sp:
                    issue("extended_kit_cheaper",ch,big,bp,sp,other=small,
                          comment="Большая комплектация дешевле точного поднабора")
        keys=list(bom)
        for i,s1 in enumerate(keys):
            if s1==big or s1 not in prices[ch]:continue
            for s2 in keys[i+1:]:
                if s2==big or s2 not in prices[ch]:continue
                if bom[s1]+bom[s2]==B:
                    total=prices[ch][s1]+prices[ch][s2]
                    if bp>=total-0.5:
                        issue("two_different_kits_cheaper",ch,big,bp,total,
                          [{"sku":s1,"qty":1,"unit":prices[ch][s1]},{"sku":s2,"qty":1,"unit":prices[ch][s2]}],
                          other=s1+" + "+s2,comment="Состав совпадает с суммой двух малых наборов")

# Equal BOM but different sale prices.
exact=defaultdict(list)
for k,p in bom.items():exact[tuple(sorted(p.items()))].append(k)
for group in exact.values():
    if len(group)<2:continue
    for ch in all_ch:
        pairs=[(k,prices[ch][k]) for k in group if k in prices[ch]]
        if len(pairs)<2:continue
        hi=max(pairs,key=lambda x:x[1]);lo=min(pairs,key=lambda x:x[1])
        if hi[1]>lo[1]*1.1 and hi[1]-lo[1]>100:
            issue("equal_bom_different_prices",ch,hi[0],hi[1],lo[1],other=lo[0],
                  comment="Полностью одинаковый состав по BOM; проверить качество и название")

# Actual Avito snapshot vs current planned prices.
for sku,p in prices["avito_config"].items():
    v=prices["avito_feed"].get(sku)
    if v is not None and abs(v-p)>0.5:
        issue("avito_feed_mismatch","avito_feed",sku,v,p,comment="В фиде цена не равна управляющему документу")
    v=prices["avito_live"].get(sku)
    if v is not None and abs(v-p)>0.5:
        issue("avito_listing_mismatch","avito_live",sku,v,p,comment="Активное объявление отличается от мастер-цены")
fold=defaultdict(list)
for k in m:fold[k.casefold()].append(k)
for lk,skus in fold.items():
    if len(skus)>1:quality.append({"type":"case_collision","key":lk,"skus":skus})
for k,planned in prices["site_config"].items():
    if len(fold[k.casefold()])!=1:continue
    v=money(site_snap.get(k.casefold()))
    if v and abs(planned-v)>0.5:
        issue("site_snapshot_mismatch","site_snapshot",k,v,planned,
              comment="Снимок Tilda отличается от управляющей цены; проверить актуальность снимка")
# Cost floor is a diagnostic only: some commissions and taxes still to deduct.
for k,parts in bom.items():
    if not all(m.get(p,{}).get("cost") for p in parts):continue
    cost=sum(m[p]["cost"]*q for p,q in parts.items())
    for ch in ("ozon","wb","market_intended","avito_config","site_config"):
        p=prices[ch].get(k)
        if p and p<cost*1.15:
            issue("cost_floor_risk",ch,k,p,cost,comment="Цена менее 115% суммы закупок деталей, до комиссий")

priority={"kit_more_than_parts":0,"two_small_kits_cheaper":1,"two_different_kits_cheaper":2,
          "avito_listing_mismatch":3,"avito_feed_mismatch":4,"equal_bom_different_prices":5,
          "extended_kit_cheaper":6,"cost_floor_risk":7,"kit_no_discount":8,"site_snapshot_mismatch":9}
issues.sort(key=lambda x:(priority.get(x["kind"],99),-abs(x["delta"]),x["channel"],x["sku"]))
count={"master_unique_skus":len(m),"kits":len(bom),"bom_rows":sum(len(x) for x in bom.values()),
       "comparable_by_channel":dict(coverage),"issues":len(issues),
       "issues_by_type":dict(Counter(x["kind"] for x in issues)),
       "issues_by_channel":dict(Counter(x["channel"] for x in issues)),
       "bom_coverage_missing_prices":len(missing),"data_quality":len(quality)}
report={"status":"verified_read_only","generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":"repository master and channel feeds/snapshots; marketplace and site are not live verified",
        "changed_product_prices":False,"changed_stock":False,"count":count,
        "issues":issues,"all_kit_comparisons":comparison,"missing":missing,"quality":quality,
        "bom":{k:dict(v) for k,v in bom.items()},"recent_price_change_skus":sorted(changed),
        "price_matrix":{k:{"name":v["name"],"cost":v["cost"],"fixed_avito":bool(overrides.get(k,{}).get("fixed")),
          **{ch:prices[ch].get(k) for ch in all_ch},"site_snapshot":money(site_snap.get(k.casefold()))} for k,v in m.items()}}
(OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

names={"kit_more_than_parts":"Набор дороже деталей",
       "two_small_kits_cheaper":"Большой набор дороже двух малых",
       "two_different_kits_cheaper":"Большой набор дороже двух разных малых",
       "avito_listing_mismatch":"Цена Авито на витрине отличается",
       "avito_feed_mismatch":"Цена в фиде Авито отличается",
       "equal_bom_different_prices":"Идентичный состав с разной ценой",
       "extended_kit_cheaper":"Большой набор дешевле меньшего",
       "cost_floor_risk":"Риск маржи при цене ниже 115% закупки",
       "kit_no_discount":"Комплект без экономии",
       "site_snapshot_mismatch":"Цена снимка сайта отличается"}
md=["# LEOPARTS — проверка связанных цен (22 ноября 2026)","",
"Только чтение. Товарные цены, остатки и публикации не изменялись.",
"",
"## Структура",f"Мастер: {len(m)} SKU; комплектов: {len(bom)}; строк состава: {sum(map(len,bom.values()))}.",
"Полностью сопоставимых комплектов по каналам: "+", ".join(f"{c}: {n}" for c,n in coverage.items())+".",
"Сравнено только точное совпадение BOM, не похожие названия.",
"Источник Ozon/WB: сформированные файлы выгрузки; Авито: фид и снимок объявлений; сайт: оверрайды и прежний снимок; Маркет: цена в мастере (не живой API).",
"",
"## Обнаруженные вопросы"]
for i,x in enumerate(issues,1):
    if i>150:break
    md.extend(["",
      str(i)+". **"+names.get(x["kind"],x["kind"])+"** — "+x["channel"]+" / "+x["sku"],
      "Цена "+str(x["price"])+" ₽ против "+str(x["comparison"])+" ₽, разница "+str(x["delta"])+" ₽.",
      x["comment"]+("." if x["comment"] else "")+
      (" Альтернатива "+str(x["other"])+"." if x["other"] else "")+
      (" Ручная фиксированная цена — не менять автоматически." if x["fixed"] else ""),
      ("Состав: "+", ".join(str(v["qty"])+" × "+v["sku"] for v in x["parts"])+".") if x["parts"] else "" ])
md.extend(["","## Ограничения","Разница между подготовленной ценой и живой площадкой возможна из-за акций, задержки фида или недействующей автозагрузки.",
            "Не менять цены без сверки реальной комплектации и себестоимости. Полная матрица: audit.json."])
(OUT/"REPORT_RU.md").write_text("\n".join(md)+"\n",encoding="utf-8")
print("PRICE_AUDIT_OK",json.dumps(count,ensure_ascii=False),flush=True)
print("TOP",json.dumps(issues[:12],ensure_ascii=False)[:6500],flush=True)
