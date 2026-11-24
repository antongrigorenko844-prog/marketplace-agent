"""Prevent duplicate legacy content job for explicitly scoped description commits."""
import copy
import json
import subprocess
import sys

MANIFEST='data/content_manifest.json'
REQUEST='data/description_sync_request.json'
PIPELINE='data/content_pipeline_request.json'
LEGACY_PATHS={'data/master_control.xlsx','data/content_updates.json',
              'data/content_sync_request.json','data/market_video_sources.json',
              'data/package_dimensions.json','content_sync.py','content_manifest.py','content_transport.py'}

def non_description_projection(manifest):
    data=copy.deepcopy(manifest)
    if data.get('version')!=1 or not isinstance(data.get('products'),dict):
        raise ValueError('Invalid manifest')
    for sku,row in list(data['products'].items()):
        for channel,fields in list(row.get('text',{}).items()):
            fields.pop('description',None)
            if not fields:row['text'].pop(channel)
        if row.get('text')=={}:row.pop('text')
        if not row:data['products'].pop(sku)
    return data

def description_map(manifest):
    return {(sku,ch):fields['description'] for sku,row in manifest['products'].items()
            for ch,fields in row.get('text',{}).items() if 'description' in fields}

def skip_legacy(changed,previous,current,request):
    """Skip only when a complete explicit request covers a pure text change."""
    paths=set(changed)
    if not {MANIFEST,REQUEST}<=paths:return False
    if paths&LEGACY_PATHS or any(p.startswith(('photos/','photos_avito/','data/ozon_rich/')) for p in paths):return False
    if request.get('mode')!='apply':return False
    skus=request.get('skus')
    channels=request.get('channels')
    if not isinstance(skus,list) or not skus or len(skus)>50 or not isinstance(channels,list) or not channels:return False
    if any(not isinstance(s,str) or not s or s not in current['products'] for s in skus):return False
    if non_description_projection(previous)!=non_description_projection(current):return False
    before,after=description_map(previous),description_map(current)
    # No canonical description deletion may be hidden.
    if set(before)-set(after):return False
    modified={key[0] for key in before.keys()|after.keys() if before.get(key)!=after.get(key)}
    return bool(modified and modified<=set(skus))

def queue_covers(changed, previous, current, config):
    paths=set(changed)
    if not config.get('enabled') or config.get('version')!=1 or MANIFEST not in paths:return False
    if paths&LEGACY_PATHS or any(p.startswith(('photos/','photos_avito/','data/ozon_rich/')) for p in paths):return False
    if non_description_projection(previous)!=non_description_projection(current):return False
    old,new=description_map(previous),description_map(current)
    return bool(set(old)<=set(new) and old!=new)

def from_git(before,after):
    changed=subprocess.check_output(['git','diff','--name-only',before,after],text=True).splitlines()
    def get(rev,path):
        return json.loads(subprocess.check_output(['git','show',rev+':'+path],text=True))
    if MANIFEST not in changed:return False
    try:
        config=get(after,PIPELINE)
    except (subprocess.CalledProcessError,ValueError):
        config={}
    previous,current=get(before,MANIFEST),get(after,MANIFEST)
    if queue_covers(changed,previous,current,config):return True
    if REQUEST not in changed:return False
    return skip_legacy(changed,previous,current,get(after,REQUEST))

if __name__=='__main__':
    try:
        print('false' if from_git(sys.argv[1],sys.argv[2]) else 'true')
    except Exception:
        # Preserve the established legacy workflow on uncertain scope.
        print('true')
