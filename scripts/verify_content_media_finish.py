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
b=ym.get_business_id()
seal_before=read_rows(b,'offer-mappings',['0am325066ac']);save('case-sensitive-seal-before',seal_before)
seal_cards=read_rows(b,'offer-cards',['0am325066ac']);save('case-sensitive-seal-card-before',seal_cards)
if seal_before[0]['offer'].get('videos'):
 # Official deleteParameters removes only video links; preserve typed attributes.
 payload=preserve_typed_parameters(b,[{'offer':{'offerId':'0am325066ac','deleteParameters':['VIDEOS']}}])
 save('case-sensitive-seal-request',payload)
 status,data=ym._request('POST',f'/v2/businesses/{b}/offer-mappings/update',json_body={'offerMappings':payload})
 save('case-sensitive-seal-response',{'http':status,'body':data})
 assert status==200 and data.get('status')=='OK',(status,data)
 for _ in range(24):
  seal_after=read_rows(b,'offer-mappings',['0am325066ac'])
  if not seal_after[0]['offer'].get('videos'):break
  time.sleep(5)
 save('case-sensitive-seal-after',seal_after)
 before=seal_before[0]['offer'];after=seal_after[0]['offer']
 assert not after.get('videos'),'Wrong seal video still attached'
 for k in ['name','description','pictures','vendor','basicPrice','purchasePrice','weightDimensions','barcodes','params']:
  assert before.get(k)==after.get(k),('seal unexpected change',k)
 ac=read_rows(b,'offer-cards',['0am325066ac']);save('case-sensitive-seal-card-after',ac)
 assert grouped(seal_cards[0].get('parameterValues',[]))==grouped(ac[0].get('parameterValues',[])),'seal attributes changed'
 save('seal-removal-result',{'removed_wrong_video':True,'other_fields_preserved':True})
ids=list(json.load(open('data/market_content_repair_20261115.json')));cards=read_rows(b,'offer-cards',ids);save('market-ratings',cards)
video_ids=['0am325025HX','0am325025HXF','0am325025u','0am325025z','0am325066AC','0am325091e','0am325091e-set','0am325091f','0AM325467J','0AM927769G','0AM927769K','0AM927769E']
mr=read_rows(b,'offer-mappings',video_ids);save('market-videos',mr)
wc=wb.list_cards();save('wb-cards',wc);skus=['0AM927769D','0am325025','0am325025m'];oc=oz.get_product_names(skus);save('ozon-cards',oc)
res={'market_videos':{r['offer']['offerId']:[v.get('uploadState')for v in r['offer'].get('mediaFiles',{}).get('videos',[])]for r in mr},'wb_videos':{r['vendorCode']:bool(r.get('video'))for r in wc if r['vendorCode']in skus},'at':datetime.datetime.now(datetime.timezone.utc).isoformat()};save('result',res);print(json.dumps(res,ensure_ascii=False))
