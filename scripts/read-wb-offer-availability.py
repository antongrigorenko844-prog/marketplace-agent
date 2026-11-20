"""Read the alternate Wildberries warehouse report and recheck the single seller offer."""
import os,time,uuid
import requests
NM, CH, BAR = 1949483512, 3422632199, "2059304407631"
headers={"Authorization":os.environ["WB_API_TOKEN"]}
def get(endpoint,params=None):
    return requests.get("https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains"+endpoint,params=params,headers=headers,timeout=35,allow_redirects=False)
r=requests.post("https://marketplace-api.wildberries.ru/api/v3/stocks/1627258",headers=headers,json={"chrtIds":[CH]},timeout=35,allow_redirects=False)
print("FBS fresh HTTP",r.status_code,flush=True)
if r.status_code==200:
    rows=r.json()["stocks"]
    assert all(x.get("chrtId")==CH or x.get("sku")==BAR for x in rows)
    print("FBS latest available",sum(int(x["amount"]) for x in rows),flush=True)
r=get("",{"groupByNm":"true","groupByBarcode":"true","locale":"ru"})
print("FBW alternate report HTTP",r.status_code,flush=True)
if r.status_code==200:
    task=str(uuid.UUID(r.json()["data"]["taskId"]))
    for _ in range(5):
        time.sleep(11)
        state=get("/tasks/"+task+"/status")
        if state.status_code!=200:
            print("FBW alternate status HTTP",state.status_code,flush=True)
            break
        status=state.json()["data"]["status"]
        print("FBW report state",status,flush=True)
        if status=="done":
            final=get("/tasks/"+task+"/download")
            print("FBW report download HTTP",final.status_code,flush=True)
            if final.status_code==200:
                arr=final.json()
                assert isinstance(arr,list)
                sel=[a for a in arr if a.get("nmId")==NM]
                assert all(str(a.get("barcode"))==BAR for a in sel)
                if not sel:
                    print("FBW complete report contains no stock row for exact offer",flush=True)
                else:
                    print("FBW available totals",[(w["warehouseName"],int(w["quantity"])) for a in sel for w in a["warehouses"] if w.get("warehouseName")=="Всего находится на складах"],flush=True)
            break
        if status in ("canceled","expired","purged"):break
