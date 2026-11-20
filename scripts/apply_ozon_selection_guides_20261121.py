"""Read the official score for this batch after publication."""
import json
import ozon_client as oz

offers = ["0am325091f-set","0am325091e-set","0am325091f","0am325091e","0am325025hfe","prokladka-vtulki-dq200","salnik-tolkatelya-alum","0AM325443D","0AM325587E","salniki-shtokov-2sht","0am325066ae","0AM325433E","0am325025ae","0am325025z","0am325025u","salnikprivod-l","tolkateli-4-salniki-4","vtulki-shtokov-2sht","salnikvhod","salnikprivod-r","kryshka-mehatronika-dq200","0am325025j","0am325025G","0am325025f","WHT001922","01X301127C","0am325025l","0AM325413A","0am325025q","0am325066ad","0am325025P","Dq200dsg7"]
cards = oz.get_product_names(offers)
ids = {int(c['sku']):c['offer_id'] for c in cards}
if set(ids.values()) != set(offers):
    raise ValueError('Expected a complete exact-offer match')
response = oz._post('/v1/product/rating-by-sku', {'skus':list(ids)})
scores = {ids[int(p['sku'])]:p['rating'] for p in response.get('products',[])}
if set(scores) != set(offers):
    raise ValueError('Incomplete official scores')
print('OZON_SCORE_RECEIPT '+json.dumps(scores))
