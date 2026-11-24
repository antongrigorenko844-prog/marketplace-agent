"""Use existing content-only transport and adapters, never other fields."""
import json,sys,datetime
from pathlib import Path
import content_sync as cs
from content_manifest import load
from content_transport import guard
SKU="0am325025H"
OUT=Path("data/content_mechatronic_h_20261124_result.json")
def save(j):OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def main():
    if OUT.exists():raise RuntimeError("Previous attempt exists; no blind replay")
    rule=load()[SKU]
    text=rule["text"]
    rules={ch:{"text":{ch:{"description":text[ch]["description"]}}} for ch in ("ozon","wb","market")}
    assert all("Соленоиды — каждый проверен на стенде" in text[ch]["description"] for ch in rules)
    assert all("гальваническим покрытием" in text[ch]["description"] for ch in rules)
    assert len(text["wb"]["description"])<=2000
    j={"sku":SKU,"status":"started","writes":[],"channels":{},"price_writes":0,"stock_writes":0}
    save(j)
    with guard(j["writes"],allow_write=False):
        before=cs.capture([SKU],include_aux=False)
    j["before"]={}
    for ch in rules:
        b=before.get(ch,{})
        if b.get("error"):
            j["before"][ch]={"error":b["error"]};continue
        if ch=="ozon":
            row=cs.one(b["cards"],"offer_id",SKU)
            j["before"][ch]={"description":next((a.get("values",[{}])[0].get("value","") for a in row.get("attributes",[]) if a["id"]==4191 and a.get("values")),""),"name":row.get("name")}
        elif ch=="wb":
            row=cs.one(b["cards"],"vendorCode",SKU)
            j["before"][ch]={"description":row.get("description"),"name":row.get("title"),"nmID":row.get("nmID")}
        else:
            row=cs.one([x["offer"] for x in b["offers"]],"offerId",SKU)
            j["before"][ch]={"description":row.get("description"),"name":row.get("name")}
    save(j)
    # Persist old descriptions and intent before any external write.
    import subprocess
    subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True,timeout=180)
    with guard(j["writes"],allow_write=True):
        for ch,fn in (("ozon",cs.apply_ozon),("wb",cs.apply_wb),("market",cs.apply_market)):
            try:
                if before[ch].get("error"):raise RuntimeError(before[ch]["error"])
                result=fn(SKU,rules[ch],before[ch])
                j["channels"][ch]={k:v for k,v in result.items() if k in ("verified","changed","photos","cover_preserved")}
            except Exception as e:j["channels"][ch]={"verified":False,"error":str(e)[:600]}
            save(j)
    j["status"]="verified" if all(x.get("verified") for x in j["channels"].values()) and len(j["channels"])==3 else "partial"
    j["finished_utc"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(j)
    return 0 if j["status"]=="verified" else 1
if __name__=="__main__":sys.exit(main())
