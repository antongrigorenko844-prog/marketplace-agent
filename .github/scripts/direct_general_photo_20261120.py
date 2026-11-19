"""Attach only the exact user-selected catalog photo to the existing GENERAL ad.

Allowed writes: upload the verified image; set this ad's image list; submit the
same ad for moderation only if needed. No keyword/campaign/catalog mutations.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request

CID = 716316525
TARGET = 1927488453443618167
GROUP = 5828476541
BASE = 'https://api.direct.yandex.com/json/v501/'
OUT = Path('data/direct_general_photo_20261120_result.json')
SCHEMA = Path('data/direct_all234_keywords_plan_20261120.json')
SCHEMA_SHA = '077f0b4c500c9f035f9e5c6feecf231fe7e0f4685090a876a8bab267353db3e8'
PHOTO = Path('photos/0am325025H/0am325025H-1.png')
PHOTO_SHA = '0593c687c24aace137c34a19a0e7670d0d9d7f0d4d210136892942c2832a20c9'
PHOTO_GIT_SHA = 'e24cf5f427dec188f96fb7cac4a1cd03d9326229'
PHOTO_NAME = 'LEOPARTS DQ200 user-selected catalog photo 2026-11-20'
TITLE = 'Мехатроник DSG7 DQ200'
TEXT = 'Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ'
HREF = 'https://leoparts-akpp.ru/mehatronik-dq200'
READ_KEYS = {'campaigns': 'Campaigns', 'adgroups': 'AdGroups', 'keywords': 'Keywords', 'ads': 'Ads'}
MAX_BYTES = 4 * 1024 * 1024
APPROVED_HASH = None


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(obj):
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()
                if str(k).lower() not in {'authorization', 'token', 'password', 'client-login', 'login', 'imagedata'}}
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


def guard(service, method, params):
    if method == 'get' and service in READ_KEYS:
        selection = {'Ids': [CID]} if service == 'campaigns' else {'CampaignIds': [CID]}
        if params.get('SelectionCriteria') != selection:
            raise RuntimeError('Read outside target campaign')
    elif service == 'adimages' and method == 'get':
        if not APPROVED_HASH or params.get('SelectionCriteria') != {'AdImageHashes': [APPROVED_HASH]}:
            raise RuntimeError('Image read outside approved hash')
    elif service == 'adimages' and method == 'add':
        items = params.get('AdImages', [])
        if set(params) != {'AdImages'} or len(items) != 1:
            raise RuntimeError('Upload must contain exactly one image')
        item = items[0]
        if set(item) != {'ImageData', 'Type', 'Name'} or item['Type'] != 'AUTO' or item['Name'] != PHOTO_NAME:
            raise RuntimeError('Image metadata differs from approved input')
        raw = base64.b64decode(item['ImageData'], validate=True)
        if hashlib.sha256(raw).hexdigest() != PHOTO_SHA:
            raise RuntimeError('Image bytes differ from user-selected original')
    elif service == 'ads' and method == 'update':
        expected = {'Ads': [{'Id': TARGET, 'ResponsiveAd': {'AdImageHashes': {'Items': [APPROVED_HASH]}}}]}
        if not APPROVED_HASH or params != expected:
            raise RuntimeError('Only the target ad image attachment may be updated')
    elif service == 'ads' and method == 'moderate':
        if params != {'SelectionCriteria': {'Ids': [TARGET]}}:
            raise RuntimeError('Moderation outside approved ad')
    else:
        raise RuntimeError('Operation outside user approval')


def call(service, method, params):
    guard(service, method, params)
    token = os.environ.get('YANDEX_DIRECT_TOKEN', '')
    if not token:
        raise RuntimeError('Direct credential unavailable')
    headers = {'Authorization': 'Bearer ' + token,
               'Content-Type': 'application/json; charset=utf-8', 'Accept-Language': 'en'}
    if os.environ.get('YANDEX_DIRECT_LOGIN'):
        headers['Client-Login'] = os.environ['YANDEX_DIRECT_LOGIN']
    request = urllib.request.Request(BASE + service,
        data=json.dumps({'method': method, 'params': params}, ensure_ascii=False).encode(),
        headers=headers, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=40) as response:
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


def read_specs():
    raw = SCHEMA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA:
        raise RuntimeError('Read schema changed')
    specs = {}
    for spec in json.loads(raw)['preflight_read_only_requests']:
        service = spec['endpoint'].removeprefix(BASE)
        if service in READ_KEYS:
            if spec['method'] != 'get' or spec['endpoint'] != BASE + service:
                raise RuntimeError('Invalid read schema')
            specs[service] = copy.deepcopy(spec['params'])
    if set(specs) != set(READ_KEYS):
        raise RuntimeError('Incomplete preflight schema')
    if 'AdImages' not in specs['ads']['ResponsiveAdFieldNames']:
        specs['ads']['ResponsiveAdFieldNames'].append('AdImages')
    return specs


def snapshot(specs):
    data = {}
    for service, key in READ_KEYS.items():
        data[service] = sorted(call(service, 'get', specs[service])[key], key=lambda x: x['Id'])
        if any((x['Id'] if service == 'campaigns' else x['CampaignId']) != CID for x in data[service]):
            raise RuntimeError('Returned object outside approved campaign')
    return {'at_utc': now(), 'objects': data}


def target(snapshot_data):
    rows = [x for x in snapshot_data['objects']['ads'] if x['Id'] == TARGET]
    if len(rows) != 1:
        raise RuntimeError('Target ad not found exactly once')
    return rows[0]


def image_hashes(ad):
    return [x['ImageHash'] for x in (ad.get('ResponsiveAd', {}).get('AdImages') or {}).get('Items', [])]


def content_only(ad):
    ad = copy.deepcopy(ad)
    ad.pop('Status', None)
    ad.pop('StatusClarification', None)
    ad.pop('State', None)
    body = ad.get('ResponsiveAd', {})
    body.pop('AdImages', None)
    body.pop('SitelinksModeration', None)
    for field, text_key in [('Titles', 'Title'), ('Texts', 'Text')]:
        if field in body:
            body[field] = [x[text_key] for x in body[field]]
    return ad


def verify(before, after):
    b, a = before['objects'], after['objects']
    ad = target(after)
    def other_ads(xs):
        return [{k: v for k, v in x.items() if k != 'StatusClarification'} for x in xs if x['Id'] != TARGET]
    return {
        'selected_image_attached': image_hashes(ad) == [APPROVED_HASH],
        'target_title_text_url_sitelinks_unchanged': content_only(target(before)) == content_only(ad),
        'target_remains_enabled': ad['State'] == 'ON',
        'other_ads_unchanged': other_ads(b['ads']) == other_ads(a['ads']),
        'ad_inventory_unchanged': {x['Id'] for x in b['ads']} == {x['Id'] for x in a['ads']},
        'campaign_budgets_settings_negatives_unchanged': b['campaigns'] == a['campaigns'],
        'keywords_bids_autotargeting_unchanged': b['keywords'] == a['keywords'],
        'groups_unchanged': [{k: v for k, v in x.items() if k != 'Status'} for x in b['adgroups']] == [{k: v for k, v in x.items() if k != 'Status'} for x in a['adgroups']],
        'component_ads_still_suspended': all(x['State'] == 'SUSPENDED' for x in a['ads'] if x['Id'] in {1927493554789861877, 1927493554789861878}),
    }


def action_ok(result, key):
    rows = result.get(key, [])
    return len(rows) == 1 and rows[0].get('Id') == TARGET and not rows[0].get('Errors')


def run():
    global APPROVED_HASH
    previous = json.loads(OUT.read_text()) if OUT.exists() else {}
    report = {'status': 'preflight', 'started_at_utc': now(), 'campaign_id': CID, 'ad_id': TARGET,
              'approval': 'User selected this catalog photo and instructed to use it for the general mechatronics ad.',
              'image_path': str(PHOTO), 'image_sha256': PHOTO_SHA, 'workflow_commit': os.environ.get('GITHUB_SHA'),
              'mutations_attempted': 0, 'errors': []}
    save(report)
    try:
        raw = PHOTO.read_bytes()
        git_sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if hashlib.sha256(raw).hexdigest() != PHOTO_SHA or git_sha != PHOTO_GIT_SHA:
            raise RuntimeError('Selected source photo changed')
        if raw[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', raw[16:24]) != (1200, 1600):
            raise RuntimeError('Selected image format/dimensions changed')
        specs = read_specs()
        before = snapshot(specs)
        report['before'] = before
        ad = target(before)
        body = ad.get('ResponsiveAd', {})
        if (ad['AdGroupId'] != GROUP or ad['Type'] != 'RESPONSIVE_AD' or ad['State'] != 'ON'
                or [x['Title'] for x in body.get('Titles', [])] != [TITLE]
                or [x['Text'] for x in body.get('Texts', [])] != [TEXT]
                or body.get('Href') != HREF or body.get('SitelinkSetId') != 1526814548):
            raise RuntimeError('Target identity/content/state changed; no write')
        prior_hash = previous.get('uploaded_image_hash') if previous.get('image_sha256') == PHOTO_SHA else None
        if image_hashes(ad):
            if not prior_hash or image_hashes(ad) != [prior_hash]:
                raise RuntimeError('Unexpected existing images; do not replace them')
            APPROVED_HASH = prior_hash
        else:
            report['mutations_attempted'] += 1
            report['status'] = 'upload_requested'
            save(report)
            result = call('adimages', 'add', {'AdImages': [{'ImageData': base64.b64encode(raw).decode(), 'Type': 'AUTO', 'Name': PHOTO_NAME}]})
            report['image_upload_result'] = result
            rows = result.get('AddResults', [])
            if len(rows) != 1 or rows[0].get('Errors') or not rows[0].get('AdImageHash'):
                raise RuntimeError('Image upload not confirmed')
            APPROVED_HASH = rows[0]['AdImageHash']
        report['uploaded_image_hash'] = APPROVED_HASH
        save(report)
        report['image_details'] = call('adimages', 'get', {'SelectionCriteria': {'AdImageHashes': [APPROVED_HASH]}, 'FieldNames': ['AdImageHash', 'OriginalUrl', 'PreviewUrl', 'Name', 'Type', 'Associated']})
        if image_hashes(ad) != [APPROVED_HASH]:
            params = {'Ads': [{'Id': TARGET, 'ResponsiveAd': {'AdImageHashes': {'Items': [APPROVED_HASH]}}}]}
            report['update_request'] = params
            report['mutations_attempted'] += 1
            report['status'] = 'attachment_requested'
            save(report)
            result = call('ads', 'update', params)
            report['update_result'] = result
            if not action_ok(result, 'UpdateResults'):
                raise RuntimeError('Image attachment response not confirmed')
        after = snapshot(specs)
        if target(after)['Status'] == 'DRAFT':
            report['mutations_attempted'] += 1
            save(report)
            result = call('ads', 'moderate', {'SelectionCriteria': {'Ids': [TARGET]}})
            report['moderate_result'] = result
            if not action_ok(result, 'ModerateResults'):
                raise RuntimeError('Moderation submission not confirmed')
            after = snapshot(specs)
        report['after'] = after
        report['verification'] = verify(before, after)
        report['final_ad_state'] = target(after)['State']
        report['final_ad_status'] = target(after)['Status']
        report['final_image_items'] = (target(after)['ResponsiveAd'].get('AdImages') or {}).get('Items', [])
        report['image_moderation_accepted'] = bool(report['final_image_items']) and all(x.get('Status') == 'ACCEPTED' for x in report['final_image_items'])
        rejected = any(x.get('Status') == 'REJECTED' for x in report['final_image_items'])
        report['status'] = ('verified' if report['image_moderation_accepted'] else 'attached_pending_moderation') if all(report['verification'].values()) and not rejected else 'verification_failed'
        report['rollback_request_if_needed'] = {'Ads': [{'Id': TARGET, 'ResponsiveAd': {'AdImageHashes': None}}]}
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
    print(json.dumps({k: report.get(k) for k in ['status', 'ad_id', 'uploaded_image_hash', 'mutations_attempted', 'final_ad_state', 'final_ad_status', 'final_image_items', 'verification', 'errors', 'finished_at_utc']}, ensure_ascii=False))
    if report['status'] not in {'verified', 'attached_pending_moderation'}:
        raise SystemExit(1)


if __name__ == '__main__':
    run()
