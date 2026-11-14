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
        assert set(row) <= {'description', 'composition', 'source', 'avito_condition', 'wb_composition', 'market_description'}, sku
        assert row.get('description') or row.get('composition'), sku
        if 'description' in row:
            assert isinstance(row['description'], str) and 100 < len(row['description']) <= 500, sku
        if 'composition' in row:
            assert isinstance(row['composition'], str) and 0 < len(row['composition']) <= 2000, sku
        if 'wb_composition' in row:
            assert isinstance(row['wb_composition'], str) and 0 < len(row['wb_composition']) <= 100, sku
        if 'market_description' in row:
            assert isinstance(row['market_description'], str) and 500 <= len(row['market_description']) <= 6000, sku
        assert row.get('avito_condition', 'Новое') in {'Новое', 'Б/у'}, sku
    return {k:v for k,v in rows.items() if not k.startswith('_')}

def description_for(sku, fallback, platform=None):
    row = load().get(sku, {})
    if platform == 'market':
        return row.get('market_description', fallback)
    return row.get('description', fallback)

def composition_for(sku, platform=None):
    row = load().get(sku, {})
    return row.get('wb_composition', row.get('composition')) if platform == 'wb' else row.get('composition')

def avito_condition_for(sku, fallback):
    return load().get(sku, {}).get('avito_condition', fallback)


def normalize_wb_composition(value):
    if value is None:
        return None
    return [' '.join(part.replace('ё', 'е').split()) for item in value for part in item.split(';') if part.strip()]
