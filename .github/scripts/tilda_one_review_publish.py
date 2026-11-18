"""One-product Tilda review publication via existing CommerceML connector.
Only import0_1.xml; never sends offers.xml, prices, quantities, or photos.
"""
import os,sys,json,time,requests
from pathlib import Path
sys.path.insert(0,'.')
from tilda_commerceml import build_import_xml,_url,_check
data=json.loads(Path("data/tilda_review_one_product_preview.json").read_text(encoding="utf-8"))
sku="0AM325025H"
assert data["sku"]==sku
assert data["title"]=="Мехатроник DSG7 DQ200 0AM/0CW ПРОВЕРЕН НА АВТО"
assert "Суетолог" in data["proposed_description"]
assert "лето на нём откатал проблем небыло! жду зиму!" in data["proposed_description"]
assert "ТЕСТОВЫЙ БЛОК" not in data["proposed_description"]
url=os.environ["TILDA_COMMERCEML_URL"]
user=os.environ["TILDA_COMMERCEML_USER"]
password=os.environ["TILDA_COMMERCEML_PASSWORD"]
assert url and user and password
item={"id":sku,"title":data["title"],"description":data["proposed_description"],"price":1,"qty":None}
xml=build_import_xml([item])
assert xml.count("<Товар>")==1 and "<Картинка>" not in xml and "<ЦенаЗаЕдиницу>" not in xml
s=requests.Session();s.auth=(user,password)
r=s.get(_url(url,type="catalog",mode="checkauth"),timeout=45)
_check(r,"checkauth")
lines=r.text.strip().splitlines()
if len(lines)>=3 and lines[1].strip() and lines[2].strip() and lines[1].strip() not in s.cookies:
 s.cookies.set(lines[1].strip(),lines[2].strip())
r=s.get(_url(url,type="catalog",mode="init"),timeout=45);_check(r,"init")
r=s.post(_url(url,type="catalog",mode="file",filename="import0_1.xml"),data=xml.encode("utf-8"),headers={"Content-Type":"application/octet-stream"},timeout=90);_check(r,"upload")
for i in range(50):
 r=s.get(_url(url,type="catalog",mode="import",filename="import0_1.xml"),timeout=90)
 response=_check(r,"import")
 if not response.lower().startswith("progress"):break
 time.sleep(5)
else:raise RuntimeError("Tilda import timeout")
assert response.lower().startswith("success"),"Tilda did not confirm success: "+response[:200]
result={"sku":sku,"import_response":response[:400],"offers_sent":False,"photos_sent":False,"single_product":True,"published":True}
Path("data/tilda_one_review_publish_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
