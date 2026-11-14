"""Verified media sources for exact Market SKUs; never guess by letter case."""
import json
from pathlib import Path
def market_videos_for(videos):
    data=json.loads((Path(__file__).resolve().parent/'data/market_video_sources.json').read_text())
    excluded=set(data.get('excluded',[]))
    return {sku:data.get('overrides',{}).get(sku,urls) for sku,urls in videos.items() if sku not in excluded}

def wb_video_for(sku, original):
    data=json.loads((Path(__file__).resolve().parent/"data/market_video_sources.json").read_text())
    return data.get("wb_overrides",{}).get(sku, original)


def wb_images_for(sku, images):
    """Keep reviewed additional images when a one-photo source is re-exported.

    An already expanded or manually replaced gallery is left untouched.
    """
    path = Path(__file__).resolve().parent / "data/wb_media_additions_20261115.json"
    if not path.exists():
        return list(images)
    extra = json.loads(path.read_text()).get(sku, [])
    result = list(images)
    if len(result) != 1 or not extra:
        return result
    for row in extra:
        if row["url"] not in result:
            result.append(row["url"])
    return result
