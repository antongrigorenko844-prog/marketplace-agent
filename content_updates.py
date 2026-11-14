"""Approved product descriptions shared by marketplace exporters.

Only text content is overridden. Prices, availability, dimensions and media
remain owned by their existing sources. Keys are exact, case-sensitive SKUs.
"""
import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent / 'data' / 'content_updates.json'

@lru_cache(maxsize=1)
def load():
    if not PATH.exists():
        return {}
    rows = json.loads(PATH.read_text())
    for sku, row in rows.items():
        if sku.startswith('_'):
            continue
        assert set(row) <= {'description', 'composition', 'source', 'avito_condition'}, sku
        assert isinstance(row.get('description'), str) and 100 < len(row['description']) <= 500, sku
        assert row.get('avito_condition', 'Новое') in {'Новое', 'Б/у'}, sku
    return {k:v for k,v in rows.items() if not k.startswith('_')}

def description_for(sku, fallback):
    return load().get(sku, {}).get('description', fallback)

def composition_for(sku):
    return load().get(sku, {}).get('composition')

def avito_condition_for(sku, fallback):
    return load().get(sku, {}).get('avito_condition', fallback)
