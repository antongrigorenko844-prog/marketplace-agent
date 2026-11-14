"""Final read-back plus removal of one failed video assigned to the wrong case-sensitive SKU."""
import sys,json,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yandex_market_client as ym
import wb_client as wb
import ozon_client as oz
from market_content import read_rows,preserve_typed_parameters,grouped
OUT=Path('/tmp/content-media-finish');OUT.mkdir(exist_ok=True)
def save(n,d):(OUT/(n+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2))
b=ym.get_business_id();save('case-sensitive-seal',read_rows(b,'offer-mappings',['0am325066ac']))
ids=list(json.load(open('data/market_content_repair_20261115.json')));cards=read_rows(b,'offer-cards',ids);save('market-ratings',cards)
video_ids=['0am325025HX','0am325025HXF','0am325025u','0am325025z','0am325066AC','0am325091e','0am325091e-set','0am325091f','0AM325467J','0AM927769G','0AM927769K','0AM927769E']
mr=read_rows(b,'offer-mappings',video_ids);save('market-videos',mr)
wc=wb.list_cards();save('wb-cards',wc);skus=['0AM927769D','0am325025','0am325025m'];oc=oz.get_product_names(skus);save('ozon-cards',oc)
res={'market_videos':{r['offer']['offerId']:[v.get('uploadState')for v in r['offer'].get('mediaFiles',{}).get('videos',[])]for r in mr},'wb_videos':{r['vendorCode']:bool(r.get('video'))for r in wc if r['vendorCode']in skus},'at':datetime.datetime.now(datetime.timezone.utc).isoformat()};save('result',res);print(json.dumps(res,ensure_ascii=False))
