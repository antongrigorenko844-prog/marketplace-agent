"""User-approved minimal corrections in the two specialist mechatronics groups.

Only two ad Hrefs may change. GENERAL, all groups, negative phrases, every
keyword, bids, budgets and other ads are read-only.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from urllib.parse import urlparse

CID = 716316525
BASE = 'https://api.direct.yandex.com/json/v501/'
LANDING = 'https://leoparts-akpp.ru/mehatronik-dq200'
OUT = Path('data/direct_mech_semantics_fix_20261120_result.json')
SCHEMA = Path('data/direct_all234_keywords_plan_20261120.json')
SCHEMA_SHA = '077f0b4c500c9f035f9e5c6feecf231fe7e0f4685090a876a8bab267353db3e8'
AD_TARGETS = {
    1927466206584354174: {'group': 5826183946, 'href': LANDING + '#s-platoi'},
    1927466195846975291: {'group': 5826171512, 'href': LANDING + '#bez-platy'},
}
MECH_GROUPS = {5828476541, 5826183946, 5826171512}
PAUSED_ADS = {1927493554789861877, 1927493554789861878}
TITLE = 'Мехатроник DSG7 DQ200'
TEXT = 'Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ'
READ_KEYS = {'campaigns': 'Campaigns', 'adgroups': 'AdGroups', 'keywords': 'Keywords', 'ads': 'Ads'}
MAX_BYTES = 4 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()
                if str(k).lower() not in {'authorization', 'token', 'password', 'client-login', 'login'}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        for name in ('YANDEX_DIRECT_TOKEN', 'YANDEX_DIRECT_LOGIN'):
            secret = os.environ.get(name, '')
            if secret:
                value = value.replace(secret, '[redacted]')
    return value


def save(report):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean(report), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def guard(service, method, params):
    if method == 'get' and service in READ_KEYS:
        wanted = {'Ids': [CID]} if service == 'campaigns' else {'CampaignIds': [CID]}
        if params.get('SelectionCriteria') != wanted:
            raise RuntimeError('Read outside campaign scope')
        return
    if service == 'ads' and method == 'update':
        rows = params.get('Ads', [])
        if set(params) != {'Ads'} or not rows or len(rows) > 2:
            raise RuntimeError('Unexpected ad update batch')
        ids = [x.get('Id') for x in rows]
        if len(set(ids)) != len(ids):
            raise RuntimeError('Duplicate ad update')
        for row in rows:
            aid = row.get('Id')
            if aid not in AD_TARGETS or row != {'Id': aid, 'ResponsiveAd': {'Href': AD_TARGETS[aid]['href']}}:
                raise RuntimeError('Only approved two Hrefs may change')
        return
    if service == 'ads' and method == 'moderate':
        ids = params.get('SelectionCriteria', {}).get('Ids', [])
        if (params != {'SelectionCriteria': {'Ids': ids}} or not ids
                or len(ids) != len(set(ids)) or not set(ids).issubset(AD_TARGETS)):
            raise RuntimeError('Moderation outside two target ads')
        return
    raise RuntimeError('Operation outside approved minimal correction')


def call(service, method, params):
    guard(service, method, params)
    token = os.environ.get('YANDEX_DIRECT_TOKEN', '')
    if not token:
        raise RuntimeError('Direct credential unavailable')
    headers = {'Authorization': 'Bearer ' + token,
               'Content-Type': 'application/json; charset=utf-8', 'Accept-Language': 'en'}
    if os.environ.get('YANDEX_DIRECT_LOGIN'):
        headers['Client-Login'] = os.environ['YANDEX_DIRECT_LOGIN']
    req = urllib.request.Request(BASE + service,
        data=json.dumps({'method': method, 'params': params}, ensure_ascii=False).encode(),
        headers=headers, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            status, raw = response.status, response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(service + '.' + method + ' HTTP ' + str(code)) from None
    except Exception:
        raise RuntimeError(service + '.' + method + ' transport failure; inspect readback before retrying') from None
    if status != 200 or len(raw) > MAX_BYTES:
        raise RuntimeError('Unexpected response status or size')
    obj = json.loads(raw)
    if obj.get('error'):
        raise RuntimeError(service + '.' + method + ' API ' + str(obj['error'].get('error_code')) + ' ' + str(obj['error'].get('error_string', '')))
    result = obj.get('result')
    if not isinstance(result, dict) or result.get('LimitedBy'):
        raise RuntimeError('Incomplete API response')
    return result


def validate_landing():
    class SameHost(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            p = urlparse(newurl)
            if p.scheme != 'https' or p.hostname != 'leoparts-akpp.ru':
                raise RuntimeError('Landing redirect outside approved HTTPS host')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    req = urllib.request.Request(LANDING, headers={'User-Agent': 'Mozilla/5.0 LEOPARTS-scoped-semantic-check'})
    with urllib.request.build_opener(SameHost()).open(req, timeout=40) as response:
        raw, status, final = response.read(MAX_BYTES + 1), response.status, response.url
    if status != 200 or len(raw) > MAX_BYTES or urlparse(final).hostname != 'leoparts-akpp.ru':
        raise RuntimeError('Landing unavailable or outside approved site')
    html = raw.decode('utf-8', errors='replace')
    anchors = {x: bool(re.search(r'(?:id|name)=[\"\x27]' + re.escape(x) + r'[\"\x27]', html))
               for x in ('bez-platy', 's-platoi')}
    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if not all(anchors.values()) or not title or 'dq200' not in title.group(1).lower():
        raise RuntimeError('Expected DQ200 landing anchors missing')
    return {'url': LANDING, 'final_url': final, 'status': status, 'anchors': anchors,
            'title': title.group(1), 'sha256': hashlib.sha256(raw).hexdigest(), 'at_utc': now()}


def read_specs():
    raw = SCHEMA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA:
        raise RuntimeError('Reviewed read schema changed')
    specs = {}
    for spec in json.loads(raw)['preflight_read_only_requests']:
        service = spec['endpoint'].removeprefix(BASE)
        if service in READ_KEYS:
            if spec['method'] != 'get' or spec['endpoint'] != BASE + service:
                raise RuntimeError('Invalid read schema')
            specs[service] = copy.deepcopy(spec['params'])
    if set(specs) != set(READ_KEYS):
        raise RuntimeError('Incomplete read schema')
    if 'AdImages' not in specs['ads']['ResponsiveAdFieldNames']:
        specs['ads']['ResponsiveAdFieldNames'].append('AdImages')
    return specs


def snapshot(specs):
    objects = {}
    for service, key in READ_KEYS.items():
        objects[service] = sorted(call(service, 'get', specs[service])[key], key=lambda x: x['Id'])
        if any((x['Id'] if service == 'campaigns' else x['CampaignId']) != CID for x in objects[service]):
            raise RuntimeError('Returned object outside campaign')
    return {'at_utc': now(), 'objects': objects}


def target_ads(snap):
    ads = {a['Id']: a for a in snap['objects']['ads'] if a['Id'] in AD_TARGETS}
    if set(ads) != set(AD_TARGETS):
        raise RuntimeError('Target ad inventory changed')
    return ads


def check_before(snap):
    ads = target_ads(snap)
    for aid, ad in ads.items():
        body, expected = ad.get('ResponsiveAd', {}), AD_TARGETS[aid]
        if (ad['AdGroupId'] != expected['group'] or ad['Type'] != 'RESPONSIVE_AD' or ad['State'] != 'ON'
                or body.get('Href') not in {LANDING, expected['href']}
                or [x['Title'] for x in body.get('Titles', [])] != [TITLE]
                or [x['Text'] for x in body.get('Texts', [])] != [TEXT]
                or body.get('SitelinkSetId') != 1526814548):
            raise RuntimeError('Target ad identity/content/state differs from approved baseline')
        if ad['Status'] != 'ACCEPTED' and not (body['Href'] == expected['href'] and ad['Status'] == 'MODERATION'):
            raise RuntimeError('Target ad not accepted or already-updated pending moderation')
    manual = [x for x in snap['objects']['keywords'] if x['Keyword'] != '---autotargeting']
    if len(manual) != 266 or sum(x['AdGroupId'] in MECH_GROUPS for x in manual) != 204:
        raise RuntimeError('Keyword inventory changed before correction')
    paused = {x['Id']: x['State'] for x in snap['objects']['ads'] if x['Id'] in PAUSED_ADS}
    if set(paused) != PAUSED_ADS or set(paused.values()) != {'SUSPENDED'}:
        raise RuntimeError('Out-of-scope component ad state changed')
    return ads


def strip_moderation(value):
    if isinstance(value, dict):
        return {k: strip_moderation(v) for k, v in value.items() if k not in {'Status', 'StatusClarification'}}
    if isinstance(value, list):
        return [strip_moderation(v) for v in value]
    return value


def stable_ads(ads, omit_target_href=False):
    rows = strip_moderation(copy.deepcopy(ads))
    if omit_target_href:
        for ad in rows:
            if ad['Id'] in AD_TARGETS:
                ad['ResponsiveAd'].pop('Href', None)
    return rows


def stable_groups(groups):
    return strip_moderation(copy.deepcopy(groups))


def verify(before, after):
    b, a = before['objects'], after['objects']
    ads = target_ads(after)
    manual = [x for x in a['keywords'] if x['Keyword'] != '---autotargeting']
    return {
        'both_target_hrefs_correct': all(ads[i]['ResponsiveAd']['Href'] == v['href'] for i, v in AD_TARGETS.items()),
        'all_other_ad_fields_and_states_unchanged': stable_ads(b['ads'], True) == stable_ads(a['ads'], True),
        'all_group_settings_and_negatives_unchanged': stable_groups(b['adgroups']) == stable_groups(a['adgroups']),
        'all_keywords_ids_phrases_bids_autotargeting_unchanged': b['keywords'] == a['keywords'],
        'campaign_budget_settings_negatives_unchanged': b['campaigns'] == a['campaigns'],
        'manual_266_preserved': len(manual) == 266,
        'mechatronics_manual_204_preserved': sum(x['AdGroupId'] in MECH_GROUPS for x in manual) == 204,
        'target_ads_remain_enabled': all(x['State'] == 'ON' for x in ads.values()),
        'component_ads_still_suspended': {x['Id'] for x in a['ads'] if x['Id'] in PAUSED_ADS and x['State'] == 'SUSPENDED'} == PAUSED_ADS,
    }


def checked_action(service, method, params, ids):
    result = call(service, method, params)
    key = 'UpdateResults' if method == 'update' else 'ModerateResults'
    rows = result.get(key, [])
    if len(rows) != len(ids) or {x.get('Id') for x in rows} != set(ids) or any(x.get('Errors') for x in rows):
        raise RuntimeError(service + '.' + method + ' response contains errors/unexpected IDs: ' + json.dumps(clean(result), ensure_ascii=False))
    return result


def run():
    report = {'status': 'preflight', 'started_at_utc': now(), 'campaign_id': CID,
              'authorization': 'User explicitly instructed to execute corrections only in the three existing mechatronics groups.',
              'workflow_commit': os.environ.get('GITHUB_SHA'), 'mutations_attempted': 0,
              'target_ad_ids': sorted(AD_TARGETS), 'target_group_ids': sorted({v['group'] for v in AD_TARGETS.values()}),
              'semantic_findings': {
                  'existing_204_mechatronics_keywords_reviewed': True,
                  'no_keyword_moves_justified': True,
                  'prophylactic_cross_negatives_applied': False,
                  'cross_negative_reason': 'Observed misrouting was not proved. Ordered negative phrases can still block a useful query such as «мехатроник с платой без клонирования платы».',
                  'confirmed_change': 'Open the existing landing page at the section matching the two specialist groups.'},
              'errors': []}
    save(report)
    try:
        specs = read_specs()
        before = snapshot(specs)
        report['before'] = before
        ads = check_before(before)
        report['landing_check'] = validate_landing()
        pending_ads = [{'Id': aid, 'ResponsiveAd': {'Href': spec['href']}}
                       for aid, spec in AD_TARGETS.items() if ads[aid]['ResponsiveAd']['Href'] != spec['href']]
        report['planned_requests'] = {'ads_update': pending_ads}
        report['rollback_requests_if_needed'] = {
            'ads': {'Ads': [{'Id': aid, 'ResponsiveAd': {'Href': ad['ResponsiveAd']['Href']}} for aid, ad in ads.items()]},
        }
        save(report)
        if pending_ads:
            report['status'] = 'ad_hrefs_update_requested'
            report['mutations_attempted'] += 1
            save(report)
            report['ads_update_result'] = checked_action('ads', 'update', {'Ads': pending_ads}, [x['Id'] for x in pending_ads])
            save(report)
        after = snapshot(specs)
        draft_ids = [aid for aid, ad in target_ads(after).items() if ad['Status'] == 'DRAFT']
        if draft_ids:
            report['mutations_attempted'] += 1
            save(report)
            report['moderate_result'] = checked_action('ads', 'moderate', {'SelectionCriteria': {'Ids': draft_ids}}, draft_ids)
            after = snapshot(specs)
        for attempt in range(3):
            report['after'] = after
            report['verification'] = verify(before, after)
            report['verification_attempts'] = attempt + 1
            save(report)
            if all(report['verification'].values()):
                break
            if attempt < 2:
                time.sleep(3)
                after = snapshot(specs)
        report['final_ads'] = [{'Id': aid, 'State': ad['State'], 'Status': ad['Status'], 'Href': ad['ResponsiveAd']['Href']}
                               for aid, ad in target_ads(after).items()]
        statuses = {x['Status'] for x in report['final_ads']}
        if all(report['verification'].values()) and statuses <= {'ACCEPTED', 'MODERATION'}:
            report['status'] = 'verified' if statuses == {'ACCEPTED'} else 'applied_pending_moderation'
        else:
            report['status'] = 'verification_failed'
    except Exception as exc:
        report['errors'].append(clean(str(exc)))
        report['status'] = 'failed'
        if 'specs' in locals():
            try:
                report['failure_readback'] = snapshot(specs)
            except Exception as read_exc:
                report['errors'].append(clean(str(read_exc)))
    report['finished_at_utc'] = now()
    save(report)
    print(json.dumps({k: report.get(k) for k in ('status', 'mutations_attempted', 'final_ads', 'verification', 'errors', 'finished_at_utc')}, ensure_ascii=False))
    if report['status'] not in {'verified', 'applied_pending_moderation'}:
        raise SystemExit(1)


if __name__ == '__main__':
    run()
