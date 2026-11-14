"""Verified media sources for exact Market SKUs; never guess by letter case."""
import json
from pathlib import Path
def market_videos_for(videos):
    data=json.loads((Path(__file__).resolve().parent/'data/market_video_sources.json').read_text())
    excluded=set(data.get('excluded',[]))
    return {sku:data.get('overrides',{}).get(sku,urls) for sku,urls in videos.items() if sku not in excluded}
