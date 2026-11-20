import json
from pathlib import Path
from leoparts_direct_operation_nov21 import site_records, avito_rows
items=json.loads(Path('data/approved_direct_prices_20261121.json').read_text())
for channel, read in [('tilda',site_records),('avito',avito_rows)]:
    try:
        rows=read()
    except Exception as err:
        print(channel, 'readback failed', type(err).__name__)
        continue
    for sku, aid, uid, avito, site in items:
        row=rows.get(uid if channel=='tilda' else str(aid),{})
        status=(('published' if row.get('sku')==sku else 'unmatched') if channel=='tilda' else row.get('status'))
        print(json.dumps(dict(channel=channel,sku=sku,observed=row.get('price'),target=site if channel=='tilda' else avito,status=status),ensure_ascii=False))
