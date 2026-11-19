"""Suspend only the two component ads explicitly approved by the user.

No campaign, group, keyword, bid, budget, feed, site, catalog or price writes.
The only permitted write is Ads.suspend for the two fixed IDs below.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request

CID = 716316525
BASE = 'https://api.direct.yandex.com/json/v501/'
OUT = Path('data/direct_suspend_components_20261120_result.json')
SCHEMA = Path('data/direct_all234_keywords_plan_20261120.json')
SCHEMA_SHA = '077f0b4c500c9f035f9e5c6feecf231fe7e0f4685090a876a8bab267353db3e8'
EXPECTED = {
    1927493554789861877: {
        'group_id': 5828501772,
        'title': 'Усиленная гидроплита мехатроника DSG7 DQ200',
        'url': 'https://leoparts-akpp.ru/tproduct/838583326453-usilennaya-gidroplita-mehatronika-dsg7-d',
    },
    1927493554789861878: {
        'group_id': 5828501773,
        'title': 'Втулка (гильза) штока мехатроника DSG7 DQ200',
        'url': 'https://leoparts-akpp.ru/tproduct/731536345633-vtulka-gilza-shtoka-mehatronika-dsg7-dq2',
    },
}
READ_KEYS = {'campaigns': 'Campaigns', 'adgroups': 'AdGroups', 'keywords': 'Keywords', 'ads': 'Ads'}
MAX_BYTES = 4 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(obj):
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()
                if str(k).lower() not in {'authorization', 'token', 'password', 'client-login', 'login'}}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, str):
        for name in ('YANDEX_DIRECT_TOKEN', 'YANDEX_DIRECT_LOGIN'):
            secret = os.environ.get(name, '')
            if secret:
                obj = obj.replace(secret, '[redacted]')
    return obj


def save(report):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean(report), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(service, method, params):
    if method == 'get':
        if service not in READ_KEYS:
            raise RuntimeError('Read service outside scope')
        expected_selection = {'Ids': [CID]} if service == 'campaigns' else {'CampaignIds': [CID]}
        if params.get('SelectionCriteria') != expected_selection:
            raise RuntimeError('Read selection outside campaign scope')
    elif service == 'ads' and method == 'suspend':
        ids = params.get('SelectionCriteria', {}).get('Ids')
        if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in ids)
                or not set(ids).issubset(EXPECTED)
                or params != {'SelectionCriteria': {'Ids': ids}}):
            raise RuntimeError('Suspend target differs from approved two ads')
    else:
        raise RuntimeError('Mutation outside explicit approval')
    token = os.environ.get('YANDEX_DIRECT_TOKEN', '')
    if not token:
        raise RuntimeError('Direct credential unavailable')
    headers = {'Authorization': 'Bearer ' + token,
               'Content-Type': 'application/json; charset=utf-8', 'Accept-Language': 'en'}
    if os.environ.get('YANDEX_DIRECT_LOGIN'):
        headers['Client-Login'] = os.environ['YANDEX_DIRECT_LOGIN']
    req = urllib.request.Request(BASE + service,
        data=json.dumps({'method': method, 'params': params}, ensure_ascii=False).encode('utf-8'),
        headers=headers, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            status = response.status
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(service + '.' + method + ' HTTP ' + str(code)) from None
    except Exception:
        raise RuntimeError(service + '.' + method + ' transport failure; do not blindly retry write') from None
    if status != 200 or len(raw) > MAX_BYTES:
        raise RuntimeError('Unexpected response status or size')
    value = json.loads(raw)
    if value.get('error'):
        raise RuntimeError(service + '.' + method + ' API error: ' + str(value['error'].get('error_code')))
    result = value.get('result')
    if not isinstance(result, dict) or result.get('LimitedBy'):
        raise RuntimeError('Incomplete API result')
    return result


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
        raise RuntimeError('Missing preflight read')
    return specs


def snapshot(specs):
    data = {}
    for service, key in READ_KEYS.items():
        result = call(service, 'get', specs[service])
        data[service] = sorted(result[key], key=lambda x: x['Id'])
        for item in data[service]:
            if (item['Id'] if service == 'campaigns' else item['CampaignId']) != CID:
                raise RuntimeError('Returned object outside approved campaign')
    return {'at_utc': now(), 'objects': data}


def check_targets(ads):
    by_id = {a['Id']: a for a in ads}
    for aid, expected in EXPECTED.items():
        ad = by_id.get(aid, {})
        body = ad.get('ResponsiveAd', {})
        if (ad.get('CampaignId') != CID or ad.get('AdGroupId') != expected['group_id']
                or ad.get('Type') != 'RESPONSIVE_AD' or body.get('Href') != expected['url']
                or [x['Title'] for x in body.get('Titles', [])] != [expected['title']]):
            raise RuntimeError('Target ad identity or advertised product changed: ' + str(aid))
        if ad.get('State') not in {'ON', 'SUSPENDED'} or ad.get('Status') != 'ACCEPTED':
            raise RuntimeError('Target state differs from approved active/paused accepted ad: ' + str(aid))
    return by_id


def stable_ads(ads, omit_target_state=False):
    result = copy.deepcopy(ads)
    for ad in result:
        ad.pop('StatusClarification', None)
        if omit_target_state and ad['Id'] in EXPECTED:
            ad.pop('State', None)
    return result


def verify(before, after):
    b, a = before['objects'], after['objects']
    amap = {x['Id']: x for x in a['ads']}
    checks = {
        'both_target_ads_suspended': all(amap.get(i, {}).get('State') == 'SUSPENDED' for i in EXPECTED),
        'ad_inventory_unchanged': {x['Id'] for x in b['ads']} == {x['Id'] for x in a['ads']},
        'other_ads_state_and_content_unchanged':
            stable_ads([x for x in b['ads'] if x['Id'] not in EXPECTED]) ==
            stable_ads([x for x in a['ads'] if x['Id'] not in EXPECTED]),
        'target_ads_only_state_changed': stable_ads(b['ads'], True) == stable_ads(a['ads'], True),
        'all_keywords_bids_and_autotargeting_unchanged': b['keywords'] == a['keywords'],
        'campaign_settings_budgets_and_negatives_unchanged': b['campaigns'] == a['campaigns'],
        'adgroup_configuration_unchanged':
            [{k: v for k, v in x.items() if k != 'Status'} for x in b['adgroups']] ==
            [{k: v for k, v in x.items() if k != 'Status'} for x in a['adgroups']],
    }
    return checks


def run():
    report = {'status': 'preflight', 'started_at_utc': now(), 'campaign_id': CID,
              'approved_target_ids': sorted(EXPECTED),
              'authorization': 'User explicitly confirmed stopping only the sleeve and hydroplate ads with reply «да».',
              'workflow_commit': os.environ.get('GITHUB_SHA'),
              'mutations_attempted': 0, 'errors': []}
    save(report)
    try:
        specs = read_specs()
        before = snapshot(specs)
        report['before'] = before
        by_id = check_targets(before['objects']['ads'])
        pending = [i for i in sorted(EXPECTED) if by_id[i]['State'] != 'SUSPENDED']
        report['requested_suspend_ids'] = pending
        save(report)
        if pending:
            report['status'] = 'suspend_requested'
            report['mutations_attempted'] = 1
            report['request'] = {'endpoint': BASE + 'ads', 'method': 'suspend',
                                 'params': {'SelectionCriteria': {'Ids': pending}}}
            save(report)
            try:
                result = call('ads', 'suspend', {'SelectionCriteria': {'Ids': pending}})
                report['suspend_result'] = result
                rows = result.get('SuspendResults', [])
                report['suspend_response_verified'] = (
                    len(rows) == len(pending) and {r.get('Id') for r in rows} == set(pending)
                    and not any(r.get('Errors') for r in rows))
                if not report['suspend_response_verified']:
                    report['errors'].append('Suspend response contains errors or unexpected IDs; inspect readback')
            except Exception as exc:
                report['errors'].append(clean(str(exc)))
            save(report)
        else:
            report['suspend_response_verified'] = True
        # Bounded read-only verification; never automatically resume or repeat a write.
        for attempt in range(4):
            after = snapshot(specs)
            report['after'] = after
            report['verification'] = verify(before, after)
            report['verification_attempts'] = attempt + 1
            save(report)
            if report['verification']['both_target_ads_suspended']:
                break
            if attempt < 3:
                time.sleep(3)
        report['status'] = 'verified' if all(report['verification'].values()) else 'verification_failed'
        report['target_final_states'] = [
            {'Id': x['Id'], 'AdGroupId': x['AdGroupId'], 'State': x['State'], 'Status': x['Status']}
            for x in report['after']['objects']['ads'] if x['Id'] in EXPECTED]
        report['manual_keywords_preserved'] = sum(x['Keyword'] != '---autotargeting' for x in report['after']['objects']['keywords'])
        report['rollback'] = 'No automatic rollback. Resume only after new explicit approval for these component ads.'
    except Exception as exc:
        report['errors'].append(clean(str(exc)))
        report['status'] = 'failed'
    report['finished_at_utc'] = now()
    save(report)
    print(json.dumps({k: report.get(k) for k in ['status', 'mutations_attempted', 'target_final_states',
                      'manual_keywords_preserved', 'verification', 'errors', 'finished_at_utc']}, ensure_ascii=False))
    if report['status'] != 'verified':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
