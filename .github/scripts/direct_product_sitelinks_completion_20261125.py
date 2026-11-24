"""Append the three previously unverified, owner-approved direct links.
Keep all existing links and all campaign settings; exact six ad IDs only.
Use isolated browser contexts and bounded retries to handle transient navigation.
"""
import asyncio,copy,json,os,sys,subprocess
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];os.chdir(ROOT)
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_product_sitelinks_20261125 as m
from direct_mass_negatives_apply_20261120 import atomic_save
BASE=ROOT/'data/direct_recovery_20261124'
OUT=BASE/'product_sitelinks_completion_20261125.json'
JOURNAL=BASE/'product_sitelinks_completion_journal_20261125.json'
OLD=json.loads((BASE/'product_sitelinks_20261125.json').read_text())
SKUS=('0am325025hx','0am325025hxg','0am927769g')
NEW_FOR={5826171512:['0am325025hx'],5826183946:['0am325025hx','0am325025hxg'],5826320196:['0am927769g'],5828476541:['0am325025hx']}
R={'campaign_id':m.CID,'status':'preflight','started_utc':m.a.now(),'site_writes':0,'feed_writes':0,'keyword_writes':0,'budget_writes':0,'bid_writes':0,'copy_writes':0,'checks':{},'groups':{},'ads':{},'errors':[],'operations':[]}
def require(x,msg):
    if not x:raise RuntimeError(msg)
def save():atomic_save(OUT,R)
def checkpoint():
    save()
    paths=[str(x.relative_to(ROOT)) for x in (OUT,JOURNAL) if x.exists()]
    subprocess.run(['bash','.github/scripts/save_data.sh',*paths],check=True,timeout=120)
def candidates():
    original=m.COPY
    try:
        m.COPY={k:original[k] for k in SKUS}
        m.R=R
        rows=m.candidate_rows()
    finally:m.COPY=original
    # Use actual canonical final URLs observed in the earlier browser test,
    # rather than constructing speculative product paths.
    for sku,row in rows.items():
        prior=OLD['checks'][sku]['tests']
        final=[t.get('final_url') for t in prior if t.get('passed')]
        urls=[]
        for u in final+row['urls']:
            if isinstance(u,str) and m.guard_url(u,row['uid']) and u not in urls:urls.append(u)
        row['urls']=urls
    return rows

async def verify(rows):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        sem=asyncio.Semaphore(2)
        async def probe(sku,row,url,device):
            attempts=[]
            for n in range(2):
                async with sem:
                    opts={'viewport':{'width':1365,'height':900}} if device=='desktop' else dict(p.devices['iPhone 13'])
                    ctx=await browser.new_context(**opts)
                    async def route_block(route):
                        host=(urlsplit(route.request.url).hostname or '').lower()
                        if any(t in host for t in ('mc.yandex','metrika.yandex','google-analytics','googletagmanager','doubleclick')):
                            await route.abort()
                        else:await route.continue_()
                    await ctx.route('**/*',route_block)
                    page=await ctx.new_page()
                    ans={'device':device,'url':url,'attempt':n+1,'passed':False}
                    responses=[]
                    def observe(response):
                        try:
                            if response.request.is_navigation_request() and response.request.frame==page.main_frame:
                                responses.append((response.url,response.status))
                        except Exception:pass
                    page.on('response',observe)
                    try:
                        try:
                            response=await page.goto(url,wait_until='domcontentloaded',timeout=22000)
                            if response:responses.append((response.url,response.status))
                        except Exception as e:
                            ans['navigation_note']=str(e).split('\n')[0][:190]
                        q={'sku':sku,'title':row['title'],'price':row['price']}
                        for i in range(9):
                            await page.wait_for_timeout(1100)
                            try:
                                dom=await page.evaluate(m.DOM,q)
                            except Exception:continue
                            ans.update(dom)
                            actual=page.url.split('?')[0].split('#')[0]
                            ans['final_url']=actual
                            ans['identity_verified']=m.guard_url(actual,row['uid'])
                            ans['http']=next((status for u,status in reversed(responses) if u.split('?')[0].split('#')[0]==actual),None)
                            expected_code='0am927769d' if sku=='0am325025hx' else '0am927769g'
                            ans['variant_verified']=expected_code in dom['title'].casefold().replace(' ','')
                            if ans['http']==200 and ans['identity_verified'] and ans['variant_verified'] and dom['title_match'] and dom['price_visible'] and dom['buy_button_visible'] and not dom['password_or_block']:
                                ans['passed']=True;break
                            if ans['http'] in (401,403,404,410) or dom['password_or_block']:break
                    except Exception as e:ans['error']=str(e).split('\n')[0][:180]
                    finally:await ctx.close()
                attempts.append(ans)
                if ans['passed']:break
                await asyncio.sleep(1)
            return attempts
        async def sku_probe(sku,row):
            alltests=[]
            for url in row['urls']:
                # Direct canonical path must succeed on both devices.
                tests=await asyncio.gather(*(probe(sku,row,url,device) for device in ('desktop','mobile')))
                alltests.extend(x for series in tests for x in series)
                if all(any(x['passed'] for x in series) for series in tests):
                    R['checks'][sku]={'eligible':True,'selected_url':url,'tests':alltests}
                    return sku,url
            R['checks'][sku]={'eligible':False,'selected_url':None,'tests':alltests}
            return sku,None
        # Three short sequences, not multiple tabs in a shared session.
        result=await asyncio.gather(*(sku_probe(k,v) for k,v in rows.items()))
        await browser.close()
    return {k:v for k,v in result if v}

def main():
    require(os.getenv('GITHUB_REF')=='refs/heads/main','main only')
    require(not JOURNAL.exists(),'Prior write journal; inspect before retry')
    require(OLD.get('protected_settings_unchanged') is True and all(v.get('saved') for v in OLD['ad_results'].values()),'Original sets unverified')
    m.current_main()
    m.d.ALLOWED=tuple(set(m.d.ALLOWED)|{'ads','feeds','sitelinks'})
    before=m.snapshot();oldsets=m.sets()
    require(before['campaign']['Id']==m.CID and before['campaign']['State']=='ON','Campaign not active')
    targets={}
    for aid,(gid,kind) in m.TARGETS.items():
        ad=before['ads'].get(aid)
        require(ad and ad['CampaignId']==m.CID and ad['AdGroupId']==gid and ad['Type']==('SHOPPING_AD' if kind=='ShoppingAd' else 'RESPONSIVE_AD'),'Ad identity changed')
        require(ad['State'] in ('ON','OFF') and ad['Status'] not in ('REJECTED','ARCHIVED','DRAFT'),'Unexpected ad state; no overwrite')
        if kind=='ShoppingAd':require(ad[kind]['FeedId']==m.a.FID and ad[kind]['FeedFilterConditions']==m.a.desired(aid),'Shopping filter changed')
        sid=ad[kind].get('SitelinkSetId')
        prior=OLD['ad_results'][str(aid)]
        links=OLD['groups'][str(gid)]['links']
        require(sid==prior['set_id'] and m.links_normalize(oldsets.get(sid,[]))==links,'Prior link set changed; no overwrite')
        targets[aid]=(gid,kind,sid,links)
    R['budget_rub']=before['campaign']['UnifiedCampaign']['BiddingStrategy']['Search']['HighestPosition']['WeeklySpendLimit']/1e6
    R['keyword_count']=len(before['keywords'])
    R['prior_states']={str(k):before['ads'][k]['State'] for k in targets}
    good=asyncio.run(verify(candidates()))
    R['verified_products']=good
    plans={}
    for aid,(gid,kind,sid,old) in targets.items():
        added={}
        for sku in NEW_FOR[gid]:
            if sku not in good:continue
            title,desc=m.COPY[sku]
            require(len(title)<=30 and len(desc)<=60,'Link length')
            added[sku]={'Title':title,'Description':desc,'Href':good[sku]}
        if not added:
            R['groups'][str(gid)]={'status':'no_verified_addition','links':old,'added_skus':[]}
            continue
        # Insert new links in product order, retaining the old relative order.
        links=copy.deepcopy(old)
        for sku in m.PREF[gid]:
            if sku not in added:continue
            following={m.COPY[k][0] for k in m.PREF[gid][m.PREF[gid].index(sku)+1:]}
            i=next((i for i,x in enumerate(links) if x['Title'] in following or x['Href'] in m.FALLBACK),len(links))
            links.insert(i,added[sku])
        require([x for x in links if x not in added.values()]==old,'Existing links modified')
        require(len(links)<=8 and len({x['Href'] for x in links})==len(links),'Link count or duplication')
        plans[aid]=links
        R['groups'][str(gid)]={'status':'planned','links':links,'added_skus':list(added),'count':len(links),'preserved_prior_links':True}
    save()
    if not plans:
        R['status']='blocked_no_verified_product_links';return
    # Take new actual baseline and refuse unrelated changes.
    m.current_main()
    fresh=m.snapshot()
    require(m.protected(fresh)==m.protected(before),'Protected settings changed')
    require(all(fresh['ads'][k][kind].get('SitelinkSetId')==sid for k,(_,kind,sid,_) in targets.items()),'Existing assignments changed')
    journal={'campaign_id':m.CID,'scope':'three missing direct links only','operations':[]}
    def intent(label,payload):
        journal['operations'].append({'operation':label,'request_sha256':m.digest(payload),'at_utc':m.a.now()})
        atomic_save(JOURNAL,journal);R['operations'].append(label);checkpoint()
    def matches(links,inventory):
        return sorted(k for k,v in inventory.items() if m.links_normalize(v)==links)
    unique=[]
    for links in plans.values():
        if links not in unique:unique.append(links)
    m.planned_new=[x for x in unique if not matches(x,oldsets)]
    if m.planned_new:
        payload={'SitelinksSets':[{'Sitelinks':v} for v in m.planned_new]}
        intent('create_completion_sets',payload)
        result=m.write_api('sitelinks','add',payload)
        require(len(result.get('AddResults',[]))==len(m.planned_new) and all(not x.get('Errors') and x.get('Id') for x in result['AddResults']),'Set creation not confirmed')
    current=m.sets()
    assigned={aid:matches(links,current)[0] for aid,links in plans.items() if matches(links,current)}
    require(set(assigned)==set(plans),'Set creation readback incomplete')
    R['new_set_ids']={str(k):v for k,v in assigned.items()}
    require(all(current.get(sid)==oldsets[sid] for _,_,sid,_ in targets.values()),'Existing set changed')
    m.current_main();fresh=m.snapshot()
    require(m.protected(fresh)==m.protected(before),'Protected settings changed before assignment')
    require(all(fresh['ads'][k][kind].get('SitelinkSetId')==sid for k,(_,kind,sid,_) in targets.items()),'Existing assignments changed before update')
    m.assignment=[{'Id':k,targets[k][1]:{'SitelinkSetId':v}} for k,v in sorted(assigned.items())]
    payload={'Ads':m.assignment}
    intent('assign_completion_sets',payload)
    result=m.write_api('ads','update',payload);entries=result.get('UpdateResults',[])
    R['assignment_response']=[{'id':str(x.get('Id')),'errors':[e.get('Code') for e in x.get('Errors',[])]} for x in entries]
    # Read back independently even if API returned partial errors.
    after=m.snapshot();inventory=m.sets()
    R['protected_settings_unchanged']=m.protected(after)==m.protected(before)
    for k,ad in after['ads'].items():
        kind=m.TARGETS.get(k,(None,'ShoppingAd' if ad.get('ShoppingAd') else 'ResponsiveAd'))[1]
        if k not in assigned:
            require(ad.get(kind,{}).get('SitelinkSetId')==before['ads'][k].get(kind,{}).get('SitelinkSetId'),'Unrelated link assignment changed')
    for k,v in assigned.items():
        ad=after['ads'][k];part=ad[targets[k][1]]
        ok=part.get('SitelinkSetId')==v and m.links_normalize(inventory.get(v,[]))==plans[k]
        R['ads'][str(k)]={'group':targets[k][0],'set_id':v,'saved':ok,'state':ad.get('State'),'status':ad.get('Status'),'sitelinks_moderation':(part.get('SitelinksModeration') or {}).get('Status'),'feed_processing':part.get('FeedProcessingStatus')}
    for gid,g in R['groups'].items():
        if g['status']=='planned':
            relevant=[v for v in R['ads'].values() if v['group']==int(gid)]
            g['status']='verified' if relevant and all(v['saved'] for v in relevant) else 'unconfirmed'
    R['original_sets_preserved']=all(inventory.get(sid)==oldsets[sid] for _,_,sid,_ in targets.values())
    require(R['protected_settings_unchanged'] and R['original_sets_preserved'],'Protection check failed')
    require({x.get('Id') for x in entries}==set(assigned) and not any(x.get('Errors') for x in entries) and all(v['saved'] for v in R['ads'].values()),'Partial or uncertain assignment')
    R['status']='verified' if all(v['state']=='ON' and v['status']=='ACCEPTED' and v['sitelinks_moderation']=='ACCEPTED' for v in R['ads'].values()) else 'saved_pending_moderation'
if __name__=='__main__':
    try:main()
    except Exception as e:
        R['errors'].append(m.d.scrub(e));R['status']='requires_review' if R['operations'] else 'blocked_no_write'
    finally:
        R['finished_utc']=m.a.now();save()
        print('Completion:',R['status'],flush=True)
