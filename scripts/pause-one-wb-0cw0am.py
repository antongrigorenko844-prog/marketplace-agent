"""Pause one verified Wildberries offer. Physical stock and reservations remain in place."""
import os, time
import requests

SKU, NM, CH, BAR = "0cw0am", 1949483512, 3422632199, "2059304407631"
C = "https://content-api.wildberries.ru"
M = "https://marketplace-api.wildberries.ru"
A = "https://seller-analytics-api.wildberries.ru"
auth = {"Authorization": os.environ["WB_API_TOKEN"]}
def call(method, base, path, body=None, params=None):
    return requests.request(method, base+path, headers=auth, json=body, params=params, timeout=30, allow_redirects=False)
def get_cards():
    res = call("POST", C, "/content/v2/get/cards/list", {"settings":{"cursor":{"limit":100},"filter":{"textSearch":SKU,"withPhoto":-1}}})
    if res.status_code != 200: raise ValueError("Cannot verify exact card: HTTP "+str(res.status_code))
    hits=[x for x in res.json()["cards"] if x["vendorCode"]==SKU]
    if len(hits)!=1 or hits[0]["nmID"]!=NM:raise ValueError("Exact current identity changed")
    sizes=hits[0]["sizes"]
    if len(sizes)!=1 or sizes[0]["chrtID"]!=CH or sizes[0]["skus"]!=[BAR]:
        raise ValueError("Live size or barcode changed")
def fbs_read(path):
    for body in ({"chrtIds":[CH]}, {"skus":[BAR]}):
        res=call("POST",M,path,body)
        if res.status_code in (400,422): continue
        if res.status_code!=200:raise ValueError("FBS read HTTP "+str(res.status_code))
        content=res.json()["stocks"]
        if not isinstance(content,list):raise ValueError("Unknown stock response")
        for v in content:
            if not(v.get("chrtId")==CH or v.get("sku")==BAR):raise ValueError("Wrong stock identity")
        return sum(int(v["amount"]) for v in content),body
    raise ValueError("No supported seller stock read format")
def seller():
    source=call("GET",M,"/api/v3/warehouses")
    if source.status_code!=200: raise ValueError("Cannot read warehouses HTTP "+str(source.status_code))
    stores=source.json()
    if not isinstance(stores,list):raise ValueError("Unexpected warehouse list")
    ids=sorted(set(int(x['id']) for x in stores if not x.get('isDeleting') and not x.get('isDeleted')))
    print("Seller warehouses",len(ids),flush=True)
    complete=0
    for i in ids:
        path="/api/v3/stocks/"+str(i)
        try:
            before,body=fbs_read(path)
            if before>0:
                item={"amount":0, "chrtId":CH} if "chrtIds" in body else {"amount":0,"sku":BAR}
                resp=call("PUT",M,path,{"stocks":[item]})
                if resp.status_code in (400,422) and 'chrtId' in item:
                    resp=call("PUT",M,path,{"stocks":[{"sku":BAR,"amount":0}]})
                print("FBS pause",i,"HTTP",resp.status_code,flush=True)
                if resp.status_code not in (200,204):continue
                time.sleep(3)
            after,_=fbs_read(path)
            print("FBS",i,"was positive",before>0,"confirmed available",after,flush=True)
            complete+= (after==0)
        except (ValueError,KeyError) as exc:
            print("FBS check",i,str(exc),flush=True)
    print("FBS confirmed zero",complete,"of",len(ids),flush=True)
def fbw():
    response=call("POST",A,"/api/analytics/v1/stocks-report/wb-warehouses",{"nmIds":[NM],"chrtIds":[CH],"limit":1000,"offset":0})
    print("FBW stock report HTTP",response.status_code,flush=True)
    if response.status_code in (200,204):
        rows=[] if response.status_code==204 else response.json()['data']["items"]
        if any(x["nmId"]!=NM or x["chrtId"]!=CH for x in rows) or len(rows)==1000:
            raise ValueError("Unverified stock response")
        print("FBW available",sum(int(x["quantity"]) for x in rows),"warehouses",len(rows),flush=True)
    else:
        fallback=call("GET","https://statistics-api.wildberries.ru","/api/v1/supplier/stocks",params={"dateFrom":"2019-01-01"})
        print("FBW stock statistics HTTP",fallback.status_code,flush=True)
        if fallback.status_code==200:
            all_rows=fallback.json()
            if not isinstance(all_rows,list) or len(all_rows)>=60000:raise ValueError("FBW statistics incomplete")
            rows=[x for x in all_rows if x.get("nmId")==NM]
            if any(x.get("barcode")!=BAR for x in rows):raise ValueError("Unexpected stock identification")
            print("FBW available, official statistics",sum(int(x["quantity"]) for x in rows),"warehouses",len(rows),flush=True)
get_cards()
print("Verified one live card",NM,flush=True)
seller()
fbw()
