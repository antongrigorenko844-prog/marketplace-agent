"""Build publish-ready Tilda article blocks and audit public SEO links; no site writes."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
import json,re,xml.etree.ElementTree as ET
import requests,markdown
from bs4 import BeautifulSoup
P=Path('seo/dq200-20261124')
HOST='https://leoparts-akpp.ru'
FILES=['p17bf-p189c-p0841','0am927769-d-e-f-g-k','gidroakkumulyator-gidroplita-remkomplekt']
H={'User-Agent':'Mozilla/5.0 (compatible; LEOPARTS SEO verification)','Accept':'text/html,application/xml,text/xml,*/*'}
def get(url):
    return requests.get(url,headers=H,timeout=25)
def inspect(url):
    try:
        r=get(url)
        soup=BeautifulSoup(r.text,'html.parser')
        canonical=soup.find('link',rel='canonical')
        robot=soup.find('meta',attrs={'name':'robots'})
        return {'url':url,'status':r.status_code,'resolved_url':r.url,'title':soup.title.get_text(strip=True) if soup.title else None,'canonical':canonical.get('href') if canonical else None,'robots':robot.get('content') if robot else None,'h1_count':len(soup.find_all('h1')),'h2_count':len(soup.find_all('h2'))}
    except Exception as e:return {'url':url,'error':type(e).__name__}
def sitemap():
    pending=[HOST+'/sitemap.xml'];seen=set();urls=set();details=[]
    while pending and len(seen)<5:
        u=pending.pop(0)
        if u in seen or urlsplit(u).hostname!='leoparts-akpp.ru':continue
        seen.add(u)
        try:
            r=get(u);r.raise_for_status()
            x=ET.fromstring(r.content)
            locs=[e.text.strip() for e in x.findall('.//{*}loc') if e.text]
            if x.tag.endswith('sitemapindex'):pending.extend(locs)
            else:urls.update(locs)
            details.append({'url':u,'status':r.status_code,'items':len(locs)})
        except Exception as e:details.append({'url':u,'error':type(e).__name__})
    return {'sources':details,'page_count':len(urls),'dq200_related':sorted(u for u in urls if re.search(r'dq200|dsg|mehatronik|gidro|0am927769|p17bf|p189c|p0841|blog|article',u,re.I) and '/tproduct/' not in u),'complete':not pending and bool(urls) and all('status' in d for d in details)}
def main():
    now=datetime.now(timezone.utc).isoformat()
    report={'started_utc':now,'status':'ready','site_writes':0,'articles':{},'sitemap':sitemap()}
    urls={HOST+'/mehatronik-dq200',HOST+'/catalog'}
    out=P/'html';out.mkdir(exist_ok=True)
    for name in FILES:
        t=(P/(name+'.md')).read_text(encoding='utf-8')
        title=re.search(r'^\*\*Title:\*\*\s*(.+)',t,re.M).group(1).strip()
        descr=re.search(r'^\*\*Description:\*\*\s*(.+)',t,re.M).group(1).strip()
        alias=re.search(r'^\*\*Планируемый URL:\*\*\s*(\S+)',t,re.M).group(1)
        body=t.split('\n',1)[0]+'\n\n'+t[t.index('\n## ')+1:]
        html=markdown.markdown(body,extensions=['tables','sane_lists'])
        # T123 body: content only; metadata set in page settings.
        (out/(name+'.html')).write_text('<article class="leoparts-seo-article" lang="ru">\n'+html+'\n</article>\n',encoding='utf-8')
        soup=BeautifulSoup(html,'html.parser')
        internal={a['href'].split('#')[0] for a in soup.find_all('a',href=True) if urlsplit(a['href']).hostname=='leoparts-akpp.ru'}
        urls|=internal
        report['articles'][name]={'planned_url':HOST+alias,'title':title,'description':descr,'h1_count':len(soup.find_all('h1')),'h2_count':len(soup.find_all('h2')),'internal_links':sorted(internal),'published':False}
    with ThreadPoolExecutor(max_workers=4) as pool:report['links']=list(pool.map(inspect,sorted(urls)))
    report['links_http_200']=sum(x.get('status')==200 for x in report['links'])
    report['links_checked']=len(report['links'])
    report['status']='publication_package_validated' if all(x['h1_count']==1 and x['h2_count']>0 and x['internal_links'] for x in report['articles'].values()) else 'invalid_structure'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    (P/'site_preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'links':report['links_checked'],'http_200':report['links_http_200'],'sitemap':report['sitemap']},ensure_ascii=False))
    return 0 if report['status']=='publication_package_validated' else 1
if __name__=='__main__':raise SystemExit(main())
