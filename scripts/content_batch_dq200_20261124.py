"""Exact-SKU description-only batch; Ozon paused pending unrelated attribute audit."""
import json,sys,datetime,subprocess
from pathlib import Path
import content_sync as cs
from content_manifest import load
from content_transport import guard
SKUS=["0am325025HX","0am325025HXE","0am325025HXF","0am325025HXG","0am325025HXK"]
OUT=Path("data/content_batch_dq200_20261124_result.json")
def save(j):OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def main():
    if OUT.exists():raise RuntimeError("Previous attempt exists; inspect before retry")
    rules=load()
    j={"status":"started","products":{},"writes":[],"price_writes":0,"stock_writes":0}
    with guard([],allow_write=False):
        before=cs.capture(SKUS,include_aux=False)
    j["before"]={ch:before.get(ch,{}) for ch in ("wb","market")}
    # Store full old card snapshots as encrypted evidence, not a public report.
    cs.encrypt_report(j)
    j["before"]={}
    for sku in SKUS:
        j["before"][sku]={}
        for ch in ("wb","market"):
            b=before.get(ch,{})
            if b.get("error"):continue
            rows=b.get("cards",[]) if ch=="wb" else [r["offer"] for r in b.get("offers",[])]
            key="vendorCode" if ch=="wb" else "offerId"
            matches=[r for r in rows if r.get(key)==sku]
            if len(matches)==1:
                row=matches[0]
                j["before"][sku][ch]={"description":row.get("description"),"name":row.get("title" if ch=="wb" else "name")}
    save(j)
    subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True,timeout=180)
    with guard(j["writes"],allow_write=True):
        for sku in SKUS:
            j["products"][sku]={}
            for ch,fn in (("wb",cs.apply_wb),("market",cs.apply_market)):
                if ch not in j["before"][sku]:
                    j["products"][sku][ch]={"verified":False,"error":"No unique live card or read failed"};continue
                try:
                    desc=rules[sku]["text"][ch]["description"]
                    assert "клонирование" in desc and "гальваническим покрытием" in desc
                    result=fn(sku,{"text":{ch:{"description":desc}}},before[ch])
                    j["products"][sku][ch]={k:v for k,v in result.items() if k in ("verified","changed","photos","cover_preserved")}
                except Exception as e:j["products"][sku][ch]={"verified":False,"error":str(e)[:600]}
                save(j)
    j["status"]="verified" if len(j["products"])==5 and all(r.get("verified") for p in j["products"].values() for r in p.values()) else "partial"
    j["finished_utc"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(j)
    return 0 if j["status"]=="verified" else 1
if __name__=="__main__":sys.exit(main())
