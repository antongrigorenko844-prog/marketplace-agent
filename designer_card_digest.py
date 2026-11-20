"""Monday card-task digest. A durable claim precedes each Telegram call."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo('Europe/Moscow')
LEDGER = Path('data/designer_card_digest.json')
LABELS = {'assigned':'выдана', 'submitted':'на проверке', 'returned':'на доработке'}
MAX_BODY = 3550  # Telegram allows 4096; leave room for week and page headings.


class DigestError(Exception):
    """A public, locally defined diagnostic."""


class Rejected(Exception):
    """A valid Telegram response explicitly rejected the request."""
    def __init__(self, code, retry_after=0):
        self.code = code
        self.retry_after = retry_after


def require(condition, code):
    if not condition:
        raise DigestError(code)


def local_day(now):
    require(now.tzinfo is not None, 'timezone_required')
    return now.astimezone(MOSCOW)


def iso_week(day):
    year, week, _ = day.isocalendar()
    return f'{year}-W{week:02d}'


def units(text):
    return len(text.encode('utf-16-le')) // 2


def render(tasks, day):
    rows, ids = [], set()
    for task in tasks:
        status = task['status']
        if status == 'accepted':
            continue
        require(status in LABELS, 'unknown_task_status')
        tid, sku = task['id'], task['sku']
        require(isinstance(tid,str) and re.fullmatch(r'[CС]-[0-9A-Fa-f]{10}',tid), 'invalid_task_identity')
        # The registry verifies the actual photos/ directory name. Preserve its
        # exact printable spelling, including spaces and nonbreaking hyphens.
        require(isinstance(sku,str) and 0 < len(sku) <= 255 and sku.isprintable()
                and sku not in ('.','..') and '/' not in sku, 'invalid_task_identity')
        require(tid not in ids, 'duplicate_task_id')
        ids.add(tid)
        platform = task['platform']
        require(platform in ('общий','Ozon','Wildberries','Яндекс Маркет','Avito','Tilda'), 'invalid_task_platform')
        ver, slide = task['version'], task['slide']
        require(type(ver) is int and ver > 0 and type(slide) is int and slide > 0, 'invalid_task_version_or_slide')
        due = date.fromisoformat(task['deadline']) if task['deadline'] else None
        label = ('без срока' if due is None else ('ПРОСРОЧЕНО ' if due < day else 'сегодня ' if due == day else 'до ') + due.strftime('%d.%m.%Y'))
        text = f'{tid} · {sku}\n{platform} · слайд {slide} · v{ver:02d} · {LABELS[status]} · {label}'
        rows.append((due or date.max,tid,text))
    overdue = sum(due < day for due, _, _ in rows)
    week = iso_week(day)
    header = f'Карточки LEOPARTS · {week}\nСрез {day:%d.%m.%Y} (МСК) · открыто {len(rows)} · просрочено {overdue}'
    bodies, body = [], ''
    for _, _, row in sorted(rows):
        if body and units(body + '\n\n' + row) > MAX_BODY:
            bodies.append(body)
            body = ''
        body = body + '\n\n' + row if body else row
    bodies.append(body or 'В реестре нет открытых задач по карточкам.')
    scope = ('В сводке — правки, зарегистрированные в боте по артикулу и слайду. '
             'Статусы старых поручений из переписки не подтверждены.\n'
             'Для списка отправьте «Мои правки». Для одной задачи — «Задача ID».')
    return [f'{header} · часть {i}/{len(bodies)}\n\n{b}\n\n{scope}' for i,b in enumerate(bodies,1)]


def read_state(root):
    path = root/LEDGER
    if not path.exists():
        return {'version':1,'weeks':{}}
    try:
        state = json.loads(path.read_bytes())
        require(state['version'] == 1 and isinstance(state['weeks'],dict), 'invalid_digest_state')
        for week, parts in state['weeks'].items():
            require(re.fullmatch(r'\d{4}-W\d{2}',week) and isinstance(parts,list) and parts, 'invalid_digest_state')
            for part in parts:
                require(part['status'] in ('pending','sending','sent','uncertain') and isinstance(part['text'],str)
                        and 0 < units(part['text']) <= 4096, 'invalid_digest_part')
        return state
    except (ValueError, TypeError, KeyError):
        raise DigestError('invalid_digest_state') from None


def persist_file(root,state):
    path = root/LEDGER
    path.parent.mkdir(exist_ok=True,parents=True)
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent,delete=False) as f:
            temp = Path(f.name)
            f.write((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode())
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def checkpoint(root,state):
    # Persist the claim on main before sending; an uncertain checkpoint stops this process.
    require(os.getenv('DESIGNER_PERSIST') == '1' and os.getenv('GITHUB_REF') == 'refs/heads/main',
            'durable_github_checkpoint_required')
    from designer_checkpoint import checkpoint as confirm
    persist_file(root,state)
    confirm()


def render_list(root, now=None):
    from designer_card_tasks import list_tasks
    return render(list_tasks(Path(root)),local_day(now or datetime.now(timezone.utc)).date())


def telegram_sender():
    import requests
    import designer_bot
    chat, token = designer_bot._chat_id(), designer_bot._token()
    require(chat and token,'designer_recipient_unavailable')
    def send(text):
        # No parse mode, no automatic retries and no redirects with the bot token.
        response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                 json={'chat_id':chat,'text':text},timeout=30,allow_redirects=False)
        data = response.json()
        if 400 <= response.status_code < 500 and data.get('ok') is False and data.get('error_code') == response.status_code:
            delay = (data.get('parameters') or {}).get('retry_after',0)
            raise Rejected(response.status_code,delay if type(delay) is int and delay >= 0 else 0)
        msg = data.get('result')
        require(response.status_code == 200 and data.get('ok') is True and isinstance(msg,dict)
                and type(msg.get('message_id')) is int and msg['message_id'] > 0, 'unconfirmed_delivery')
    return send


@contextmanager
def exclusive(root):
    fd=os.open(root,os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise DigestError('digest_busy') from None
        yield
    finally:
        os.close(fd)


def send_week(root, now, load_tasks, get_sender, save=checkpoint, *, resume_week=None):
    with exclusive(root):
        return _deliver(root,now,load_tasks,get_sender,save,resume_week)


def _deliver(root,now,load_tasks,get_sender,save,resume_week):
    local = local_day(now)
    current = iso_week(local.date())
    state = read_state(root)
    if resume_week is not None:
        require(resume_week in state['weeks'],'week_not_found')
        weeks = [resume_week]
    else:
        if local.weekday() != 0 or (local.hour, local.minute) < (9, 5):
            return {'ok':True,'scheduled':False}
        if current not in state['weeks']:
            parts = render(load_tasks(),local.date())
            state['weeks'][current] = [{'text':text,'status':'pending'} for text in parts]
            save(root,state)
        weeks = sorted(state['weeks'])  # also drain undelivered parts of older digests.
    if state.get('not_before',0) > now.timestamp():
        return dict(result(state,weeks), retry_after_seconds=int(state['not_before']-now.timestamp())+1)
    sender = None
    for week in weeks:
        for part in state['weeks'][week]:
            if part['status'] != 'pending' or part.get('not_before',0) > now.timestamp():
                continue
            if sender is None:
                sender = get_sender()  # do not claim if there is no configured recipient.
            part['status']='sending'
            part.pop('not_before',None)
            save(root,state)
            try:
                sender(part['text'])
            except Rejected as error:
                part['status']='pending'
                if error.retry_after:
                    part['not_before']=int(now.timestamp())+error.retry_after
                if error.code == 429:
                    state['not_before'] = int(now.timestamp()) + (error.retry_after or 60)
                save(root,state)
                if error.code in (401,403,429):
                    return result(state,weeks)
            except Exception:
                part['status']='uncertain'
                save(root,state)
            else:
                part['status']='sent'
                save(root,state)
    return result(state,weeks)


def result(state,weeks):
    counts=Counter(p['status'] for w in weeks for p in state['weeks'][w])
    review = [{'week':w,'part':i} for w in weeks for i,p in enumerate(state['weeks'][w],1)
              if p['status'] in ('sending','uncertain')]
    return {'ok':all(k=='sent' for k in counts),'scheduled':True,'parts':dict(counts),'review':review}


def resolve(root,week,number,delivered,save=checkpoint):
    with exclusive(root):
        state=read_state(root)
        parts=state['weeks'].get(week,[])
        require(1<=number<=len(parts) and parts[number-1]['status'] in ('sending','uncertain'),'no_uncertain_part')
        parts[number-1]['status']='sent' if delivered else 'pending'
        save(root,state)


def main():
    arg=argparse.ArgumentParser(description=__doc__)
    sub=arg.add_subparsers(dest='cmd',required=True)
    sub.add_parser('preview'); sub.add_parser('send')
    resume=sub.add_parser('resume'); resume.add_argument('--week',required=True)
    fix=sub.add_parser('resolve'); fix.add_argument('--week',required=True); fix.add_argument('--part',type=int,required=True)
    fix.add_argument('--confirmed',choices=['delivered','not-delivered'],required=True)
    args=arg.parse_args(); root=Path(__file__).resolve().parent; now=datetime.now(timezone.utc)
    try:
        from designer_card_tasks import list_tasks
        if args.cmd=='preview':
            print('\n\n'.join(render_list(root,now)))
            return 0
        if args.cmd=='resolve':
            resolve(root,args.week,args.part,args.confirmed=='delivered')
            print('Часть сводки отмечена по результату сверки.'); return 0
        summary=send_week(root,now,lambda:list_tasks(root),telegram_sender,resume_week=getattr(args,'week',None))
        print(json.dumps(summary,sort_keys=True)); return 0 if summary['ok'] else 2
    except DigestError as error:
        print(json.dumps({'error':str(error)})); return 2
    except Exception:
        print('{"error":"digest_or_storage_unavailable"}'); return 2


if __name__=='__main__':
    sys.exit(main())
