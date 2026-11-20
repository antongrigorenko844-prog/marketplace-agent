"""Versioned, single-slide production tasks. Telegram identities are supplied by the caller."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import quote


REGISTRY = Path("data/designer_card_tasks.json")
PLATFORMS = {"общий":"общий","ozon":"Ozon","озон":"Ozon","wb":"Wildberries",
             "wildberries":"Wildberries","вб":"Wildberries","яндекс маркет":"Яндекс Маркет",
             "ям":"Яндекс Маркет","avito":"Avito","авито":"Avito","tilda":"Tilda","тильда":"Tilda"}
STATUS = {"assigned":"выдана","submitted":"на проверке","returned":"на доработке","accepted":"принята"}
OUTBOX_PROBLEMS={
    "invalid_card_task":"Проверьте точный SKU, номер слайда и площадку. Для изменения укажите ID и текущую версию задачи.",
    "missing_kind":"Для поручения по слайду укажите card_task. Для обычного сообщения выберите вид note, social_post или video.",
    "invalid_packet":"Проверьте структуру JSON, адресата и ожидаемые поля сообщения.",
    "missing_attachment":"Проверьте путь и наличие приложенного файла в репозитории.",
}
ID = r"[CС]-[A-Fa-f0-9]{10}"
REF = rf"({ID})\s+[vв](\d+)"
EXTENSIONS = {"jpg","jpeg","png","webp"}
REPO="https://github.com/antongrigorenko844-prog/marketplace-agent"


class CardTaskError(ValueError):
    """Expected input or lifecycle error. Safe to show the participant."""


def _require(ok, message):
    if not ok:
        raise CardTaskError(message)


def _id(value):
    text=str(value).upper().replace("С","C")
    _require(re.fullmatch(ID,text), "Скопируйте ID целиком из карточки задания.")
    return text


def _hash(*values):
    data=json.dumps(values,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return sha256(data.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _load(root):
    file=Path(root)/REGISTRY
    if not file.exists():
        return {"version":1,"tasks":{},"events":{},"notifications":{}}
    with file.open(encoding="utf-8") as f:
        data=json.load(f)
    if (data.get("version")!=1 or
            not all(isinstance(data.get(k),dict) for k in ("tasks","events","notifications"))):
        raise RuntimeError("Повреждён реестр правок карточек.")
    return data


def list_tasks(root=Path(".")):
    """Read all managed tasks, including accepted ones. Never creates a registry."""
    return list(_load(root)["tasks"].values())


def expected_submission(task_id, version, slide, ext):
    key=_id(task_id)
    _require(type(version) is int and version>0 and type(slide) is int and slide>0,"Неверная версия или номер слайда.")
    ext=str(ext).lower().lstrip(".")
    _require(ext in EXTENSIONS,"Пришлите один файл PNG, JPEG или WebP.")
    return f"data/designer_card_submissions/{key}/v{version}/slide-{slide}.{ext}"


def submission_ref(caption):
    match=re.fullmatch(rf"\s*сдать\s+{REF}\s*",str(caption or ""),re.I)
    return (_id(match[1]),int(match[2])) if match else None


def _chunks(text,limit=3100):
    # Count escaped payload and UTF-16 units, so an HTML tag or surrogate pair is never cut.
    buf=[]; size=0
    for char in text:
        encoded=escape(char,quote=False)
        cost=len(encoded.encode("utf-16-le"))//2
        if buf and size+cost>limit:
            yield "".join(buf)
            buf=[]; size=0
        buf.append(encoded); size+=cost
    if buf:
        yield "".join(buf)


def _message(text):
    return {"kind":"text","text":text}


def _brief(task):
    ref=f"{task['id']} v{task['version']}"
    header=(f"<b>Правка карточки · {ref}</b>\nАртикул <code>{escape(task['sku'])}</code>"
            f" · слайд <b>{task['slide']}</b> · {escape(task['platform'])}"
            f"\nСтатус — {STATUS[task['status']]}")
    if task["deadline"]:
        header+="\nСрок — "+task["deadline"]
    source=task.get("source_slide") or {}
    if source.get("path"):
        label={"canonical":"Исходный слайд из каталога",
               "accepted":"Последний принятый слайд",
               "submitted":"Сданный слайд для доработки"}[source["kind"]]
        if source.get("task"):
            label+=f" · {source['task']} v{source['version']}"
        header+=f'\n<a href="{REPO}/blob/main/{quote(source["path"],safe="/")}">{escape(label)}</a>'
    else:
        header+="\n"+(source.get("note") or "Точный исходный файл слайда не установлен.")
    folder=task.get("source_folder") or "photos/"+task["sku"]
    header+=f'\n<a href="{REPO}/tree/main/{quote(folder,safe="/")}">Открыть исходные изображения SKU</a>'
    if task["status"] in ("submitted","accepted") and task.get("artifact"):
        header+=f'\n<a href="{REPO}/blob/main/{quote(task["artifact"],safe="/")}">Открыть сданный слайд v{task["version"]}</a>'
    result=[_message(header)]
    copy_label="Текст для копирования" if task["copy_text"] else "ТЗ для копирования"
    for label,value in (("Что исправить",task["brief"]),(copy_label,task["copy_text"] or task["brief"])):
        for part in _chunks(value):
            result.append(_message(f"<b>{label} · {ref}</b>\n"+(f"<pre>{part}</pre>" if label==copy_label else part)))
    if task.get("comment"):
        result.extend(_message(f"<b>Комментарий владельца · {ref}</b>\n{part}") for part in _chunks(task["comment"]))
    return result


OWNER_HELP=(
    "<b>Правки карточек</b>\nСоздайте задание сообщением по образцу\n"
    "<pre>Правка ТОЧНЫЙ-SKU слайд 2 Ozon\nЗадача: увеличить размер артикула.\n"
    "Срок: 2026-12-01\nТекст: новый текст для макета</pre>\n"
    "Площадку и срок можно пропустить. После «Текст:» вставьте все строки для копирования.\n"
    "Версию берите из задания. Команды для владельца\n"
    "<code>Изменить ID v1</code> — со следующей строки новое ТЗ и текст. "
    "При смене площадки добавьте строку «Площадка: Avito» до текста макета.\n"
    "<code>Принять ID v1</code> — одобрить файл на проверке.\n"
    "<code>Вернуть ID v1</code> — со следующей строки причина.\n"
    "<code>Мои правки</code> — список. <code>Задача ID</code> — заново открыть текст.")
DESIGNER_HELP=(
    "<b>Правки карточек</b>\n<code>Мои правки</code> покажет список. "
    "Введите <code>Задача ID</code>, чтобы открыть ТЗ и скопировать текст. "
    "Для сдачи приложите один PNG, JPEG или WebP с точной подписью <code>Сдать ID v1</code>. "
    "Подставьте ID и версию из задания. При новой версии используйте новую подпись.")


def _notice(data,key,who,blocks,task=None,assignment=False):
    data["notifications"].setdefault(key,{"to":who,"next":0,"state":"pending","blocks":blocks,
        "task":task["id"] if task else None,"version":task["version"] if task else None,
        "status":task["status"] if task else None,"assignment":assignment})


def _ref_task(data,key,version=None):
    task=data["tasks"].get(_id(key))
    _require(task is not None,"Такой задачи нет. Отправьте «Мои правки».")
    if version is not None:
        _require(type(version) is int and version==task["version"],
                 f"Устаревшая версия. Сейчас {task['id']} v{task['version']}. Отправьте «Задача {task['id']}».")
    return task


def _send_task(data,event,task):
    ref=f"{task['id']} v{task['version']}"
    blocks=_brief(task)+[_message(f"Приложите один готовый слайд как файл без сжатия с подписью <code>Сдать {ref}</code>.")]
    _notice(data,event+":designer","designer",blocks,task,assignment=True)
    _notice(data,event+":owner","owner",[_message(f"Правка зарегистрирована — <code>{ref}</code>. "
            f"Открыть ТЗ: <code>Задача {task['id']}</code>.")],task)


def _body(lines):
    brief=[];copy=[]; copying=False; deadline=None; options={}
    for line in lines:
        if not copying:
            match=re.match(r"^\s*площадка\s*:\s*(.*?)\s*$",line,re.I)
            if match:
                options["platform"]=_platform(match[1])
                continue
            match=re.match(r"^\s*срок\s*:\s*(.*?)\s*$",line,re.I)
            if match:
                deadline=match[1]
                continue
            match=re.match(r"^\s*(?:текст(?: для (?:копирования|макета))?)\s*:(?: )?(.*)$",line,re.I)
            if match:
                copying=True
                copy.append(match[1])
                continue
            line=re.sub(r"^\s*(?:задача|тз|что исправить)\s*:\s*","",line,count=1,flags=re.I)
        (copy if copying else brief).append(line)
    if copy and copy[0]=="":
        copy=copy[1:]
    if copying:
        options["copy_text"]="\n".join(copy)
    return {"brief":"\n".join(brief).strip(),"deadline":deadline,**options}


def _platform(value):
    _require(isinstance(value,str) and value.strip().casefold() in PLATFORMS,
             "Укажите площадку: Ozon, WB, Яндекс Маркет, Avito, Tilda или общий.")
    return PLATFORMS[value.strip().casefold()]


def _text_fields(fields):
    brief=fields.get("brief")
    copy=fields.get("copy_text","")
    _require(isinstance(brief,str) and brief.strip(),"Добавьте ТЗ со следующей строки. Для текста макета используйте «Текст:».")
    _require(isinstance(copy,str),"Текст макета должен быть строкой.")
    deadline=fields.get("deadline")
    if deadline:
        try:
            clean=date.fromisoformat(deadline).isoformat()
        except (TypeError,ValueError):
            raise CardTaskError("Укажите срок в формате ГГГГ-ММ-ДД.") from None
        _require(clean==deadline,"Укажите срок в формате ГГГГ-ММ-ДД.")
    return {"brief":brief.strip(),"copy_text":copy,"deadline":deadline or None}


class CardTasks:
    def __init__(self,root=Path(".")):
        self.root=Path(root).resolve()

    def get(self,key):
        data=_load(self.root)
        task=_ref_task(data,key)
        if "source_slide" not in task:
            task["source_slide"]=self._source_slide(data,task)
        return task

    def _source_folder(self,sku,platform):
        directory=self.root/"photos_avito"
        has_avito=(platform=="Avito" and directory.is_dir()
                   and any(p.name==sku and p.is_dir() and not p.is_symlink() for p in directory.iterdir()))
        return ("photos_avito/" if has_avito else "photos/")+sku

    def _submitted_source(self,task,kind):
        digest=self._artifact(task,task.get("artifact"),check_digest=True)
        return {"kind":kind,"task":task["id"],"version":task["version"],
                "path":task["artifact"],"sha256":digest}

    def _source_slide(self,data,task):
        position=(task["sku"],task["slide"],task["platform"])
        accepted=[t for t in data["tasks"].values() if t["id"]!=task["id"] and t["status"]=="accepted"
                  and (t["sku"],t["slide"],t["platform"])==position]
        if accepted:
            latest=max(t.get("accepted_at") or "" for t in accepted)
            matches=[t for t in accepted if t.get("accepted_at")==latest]
            if not latest or len(matches)!=1:
                return {"note":"Последнюю принятую версию нельзя однозначно определить. Уточните исходник у владельца."}
            try:
                return self._submitted_source(matches[0],"accepted")
            except CardTaskError:
                return {"note":"Файл принятой версии отсутствует или изменился. Запросите исходник у владельца."}
        folder=self.root/(task.get("source_folder") or self._source_folder(task["sku"],task["platform"]))
        stem=f"{task['sku']}_{task['slide']}"
        matches=[p for p in folder.iterdir() if p.stem==stem and p.suffix.lower().lstrip(".") in EXTENSIONS
                 and p.is_file() and not p.is_symlink() and p.resolve()==p] if folder.is_dir() else []
        if len(matches)!=1:
            return {"note":"Точный исходный файл слайда не найден." if not matches else
                    "Есть несколько файлов с этим номером. Уточните исходник у владельца."}
        return {"kind":"canonical","path":matches[0].relative_to(self.root).as_posix()}

    @contextmanager
    def _edit(self):
        path=self.root/REGISTRY
        path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path.parent,os.O_RDONLY)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            data=_load(self.root)
            before=json.dumps(data,sort_keys=True,ensure_ascii=False)
            yield data
            if before!=json.dumps(data,sort_keys=True,ensure_ascii=False):
                with NamedTemporaryFile("w",dir=path.parent,delete=False,encoding="utf-8") as f:
                    temp=Path(f.name)
                    try:
                        json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2)
                        f.write("\n"); f.flush(); os.fsync(f.fileno())
                        os.replace(temp,path)
                        os.fsync(fd)
                    finally:
                        temp.unlink(missing_ok=True)
        finally:
            os.close(fd)

    def _event(self,source,event_id,payload,operation):
        _require(isinstance(event_id,(int,str)) and not isinstance(event_id,bool) and str(event_id).strip(),
                 "У события нет идентификатора.")
        key=_hash(source,str(event_id))
        signature=_hash(payload)
        with self._edit() as data:
            prior=data["events"].get(key)
            if prior:
                _require(prior["hash"]==signature,"Повтор события содержит другие данные.")
                return data["tasks"].get(prior.get("task"))
            task=operation(data,key)
            data["events"][key]={"hash":signature,"task":task["id"] if task else None}
            return task

    def _create(self,data,key,fields,notify=True):
        sku=fields.get("sku")
        _require(isinstance(sku,str) and sku==Path(sku).name and sku not in ("",".",".."),
                 "Укажите точный артикул из папки photos.")
        folder=self.root/"photos"/sku
        # Check exact spelling even on case-insensitive filesystems.
        _require(folder.parent.is_dir() and any(p.name==sku and p.is_dir() and not p.is_symlink()
                 for p in folder.parent.iterdir()),"Такой точной папки SKU нет в photos.")
        slide=fields.get("slide")
        _require(type(slide) is int and slide>0,"Укажите положительный номер слайда.")
        platform=_platform(fields.get("platform","общий"))
        existing=next((t for t in data["tasks"].values()
                       if (t["sku"],t["slide"],t["platform"])==(sku,slide,platform)
                       and t["status"]!="accepted"),None)
        if existing:
            ref=f"{existing['id']} v{existing['version']}"
            _require(notify, f"Для этой позиции открыто другое задание {ref}. Сдайте файл по точному ID.")
            _notice(data,key+":owner","owner",[_message(
                f"Для этого слайда уже открыто задание <code>{ref}</code>. "
                f"Чтобы заменить ТЗ, отправьте <code>Изменить {ref}</code> и новый текст со следующей строки.")],existing)
            return existing
        identifier="C-"+key[:10].upper()
        _require(identifier not in data["tasks"],"Совпали идентификаторы разных задач. Повторите новой командой.")
        now=_now()
        task={"id":identifier,"sku":sku,"slide":slide,"platform":platform,
              "version":1,"status":"assigned","artifact":None,"artifact_sha256":None,"comment":None,
              "created_at":now,"updated_at":now,"sent_at":None,"submitted_at":None,"accepted_at":None,
              **_text_fields(fields)}
        task["source_folder"]=self._source_folder(sku,platform)
        task["source_slide"]=self._source_slide(data,task)
        data["tasks"][identifier]=task
        if notify:
            _send_task(data,key,task)
        return task

    def _revise(self,data,key,task_id,version,fields):
        task=_ref_task(data,task_id,version)
        _require(task["status"]!="accepted","Эта версия уже принята. Создайте новую правку.")
        _require(fields.get("sku",task["sku"])==task["sku"] and fields.get("slide",task["slide"])==task["slide"],
                 "SKU или слайд не совпадает с ID задачи. Проверьте адресата.")
        updated=_text_fields(fields)
        if "copy_text" not in fields:
            updated["copy_text"]=task["copy_text"]
        if "platform" in fields:
            platform=_platform(fields["platform"])
            conflict=next((t for t in data["tasks"].values() if t["id"]!=task["id"]
                          and t["status"]!="accepted" and (t["sku"],t["slide"],t["platform"])
                          ==(task["sku"],task["slide"],platform)),None)
            _require(conflict is None, "На выбранной площадке уже открыта правка этого слайда.")
            updated.update(platform=platform,source_folder=self._source_folder(task["sku"],platform))
        if updated.get("platform",task["platform"])!=task["platform"]:
            updated["source_slide"]=self._source_slide(data,{**task,**updated})
        elif task["status"]=="submitted":
            updated["source_slide"]=self._submitted_source(task,"submitted")
        elif "source_slide" not in task:
            updated["source_slide"]=self._source_slide(data,task)
        if not fields.get("deadline"):
            updated["deadline"]=task["deadline"]
        task.update(updated,version=version+1,status="assigned",artifact=None,artifact_sha256=None,comment=None,
                    updated_at=_now(),sent_at=None,submitted_at=None)
        _send_task(data,key,task)
        return task

    def ingest_outbox(self,filename,card_task):
        _require(isinstance(filename,str) and bool(filename) and isinstance(card_task,dict),
                 "Неверное описание задания outbox.")
        def apply(data,key):
            action=card_task.get("action","create")
            if action=="create":
                return self._create(data,key,card_task)
            _require(action=="edit","Для outbox поддерживаются create и edit.")
            return self._revise(data,key,card_task.get("id"),card_task.get("version"),card_task)
        return self._event("outbox",filename,card_task,apply)

    def outbox_problem(self,filename,fingerprint,reason):
        """Notify the owner once per distinct rejected packet, independently of valid ingestion."""
        name=Path(str(filename).replace("\\","/")).name
        visible_name=re.sub(r"[^\w. -]","_",name,flags=re.UNICODE)[:160] or "файл JSON"
        code=reason if isinstance(reason,str) and reason in OUTBOX_PROBLEMS else "invalid_packet"
        identity=_hash(name,str(fingerprint),code)
        message=_message(f"<b>Поручение из outbox не отправлено</b>\nФайл <code>{escape(visible_name)}</code>\n"
                         +OUTBOX_PROBLEMS[code]+" После исправления файл можно сохранить с прежним именем.")
        def problem(data,key):
            _notice(data,key,"owner",[message])
        self._event("outbox-problem",identity,code,problem)

    def adopt_legacy_ozon(self):
        """Only upon reception of a valid legacy upload; never re-issue the old brief."""
        fields={"sku":"0am325025","slide":2,"platform":"Ozon",
                "brief":"Второй слайд «Что входит». Используйте фотографии реальных деталей и сверьте состав. "
                        "Мелкие детали и их количество не дорисовывайте. Оранжевый фон, белые плашки, крупный текст.",
                "copy_text":"Усиленная гидроплита — 1 шт.\nПрокладки и уплотнительные кольца\nСальники, пыльники и фильтр"}
        def register(data,key):
            task=self._create(data,key,fields,notify=False)
            task["format_rule"]="legacy_ozon_slide"
            return task
        return self._event("legacy-ozon","ozon-0am325025-slide-2.json",fields,register)


    def _artifact(self,task,relative,check_digest=False):
        _require(isinstance(relative,(str,Path)),"Сдайте один файл с подписью задачи.")
        path=Path(relative)
        expected=expected_submission(task["id"],task["version"],task["slide"],path.suffix)
        _require(str(relative)==expected,"Файл не соответствует задаче, версии или слайду.")
        file=self.root/path
        _require(file.is_file() and file.resolve()==file,"Файл слайда отсутствует или находится вне каталога сдачи.")
        with file.open("rb") as f:
            start=f.read(12)
            valid={".png":start.startswith(b"\x89PNG\r\n\x1a\n"),".jpg":start.startswith(b"\xff\xd8\xff"),
                   ".jpeg":start.startswith(b"\xff\xd8\xff"),".webp":start[:4]==b"RIFF" and start[8:12]==b"WEBP"}
            _require(valid.get(file.suffix,False),"Содержимое должно соответствовать формату PNG, JPEG или WebP.")
            digest=sha256(start)
            for chunk in iter(lambda:f.read(1024*1024),b""):
                digest.update(chunk)
        value=digest.hexdigest()
        if check_digest:
            _require(value==task["artifact_sha256"],"Файл изменился после сдачи. Требуется повторная проверка.")
        return value

    def submit(self,role,task_id,version,event_id,artifact):
        _require(role=="designer","Сдавать файл слайда может дизайнер.")
        path=str(artifact)
        def apply(data,key):
            task=_ref_task(data,task_id,version)
            _require(task["status"] in ("assigned","returned"),"Эта версия уже сдана или принята. Откройте её по ID.")
            digest=self._artifact(task,path)
            now=_now()
            task.update(status="submitted",artifact=path,artifact_sha256=digest,submitted_at=now,updated_at=now)
            ref=f"{task['id']} v{version}"
            _notice(data,key+":owner","owner",[_message(
                f"<b>Слайд на проверке · {ref}</b>\n{escape(task['sku'])} · слайд {task['slide']} · {escape(task['platform'])}.\n"
                f"Посмотрите приложенный файл и отправьте <code>Принять {ref}</code> "
                f"или <code>Вернуть {ref}</code> с причиной со следующей строки."),
                {"kind":"file","path":path,"digest":digest,"caption":ref+" · слайд "+str(task["slide"])}],task)
            _notice(data,key+":designer","designer",[_message("Передано владельцу на проверку — <code>"+ref+"</code>.")],task)
            return task
        return self._event("telegram:designer",event_id,["submit",task_id,version,path],apply)

    def _decision(self,data,key,task_id,version,accept,comment):
        task=_ref_task(data,task_id,version)
        _require(task["status"]=="submitted","Принять или вернуть можно только сданную версию.")
        self._artifact(task,task.get("artifact"),check_digest=True)
        ref=f"{task['id']} v{version}"
        if accept:
            _require(not comment,"Для замечаний используйте команду «Вернуть».")
            now=_now()
            task.update(status="accepted",accepted_at=now,updated_at=now)
            msg=_message(f"Владелец принял слайд <code>{ref}</code> · {escape(task['sku'])}.")
            _notice(data,key+":designer","designer",[msg],task)
            _notice(data,key+":owner","owner",[msg],task)
        else:
            _require(bool(comment.strip()),"Напишите причину возврата со следующей строки.")
            task["source_slide"]=self._submitted_source(task,"submitted")
            task.update(status="returned",version=version+1,comment=comment.strip(),artifact=None,artifact_sha256=None,
                        updated_at=_now(),sent_at=None,submitted_at=None)
            _send_task(data,key,task)
        return task

    def handle_text(self,role,text,event_id):
        """Consume only task commands. Persist expected user errors for the requesting role."""
        if role not in ("owner","designer") or not isinstance(text,str) or not text.strip():
            return False
        lines=text.replace("\r\n","\n").split("\n")
        head=lines[0].strip()
        if not head:
            return False
        command=head.split(maxsplit=1)[0].casefold()
        help_request=head.casefold() in ("правки","помощь по карточкам","мои правки")
        if not help_request and command not in ("правка","изменить","принять","вернуть","задача","сдать"):
            return False
        def run(data,key):
            if help_request:
                blocks=[]
                if head.casefold()=="мои правки":
                    summaries=[f"{t['id']} v{t['version']} · {t['sku']} · слайд {t['slide']} · {t['platform']} · "
                               f"{STATUS[t['status']]} · срок {t['deadline'] or 'не указан'}"
                               for t in data["tasks"].values() if t["status"]!="accepted"]
                    blocks=[_message("<b>Открытые правки карточек</b>\n"+part)
                            for part in _chunks("\n".join(summaries) or "Открытых задач нет. Принятые можно открыть по ID.")]
                else:
                    blocks=[_message(OWNER_HELP if role=="owner" else DESIGNER_HELP)]
                _notice(data,key,role,blocks)
                return None
            if command=="задача":
                m=re.fullmatch(rf"задача\s+({ID})(?:\s+[vв](\d+))?",head,re.I)
                _require(m,"Напишите «Задача ID» или «Задача ID vN».")
                task=_ref_task(data,m[1],int(m[2]) if m[2] else None)
                if "source_slide" not in task:
                    task["source_slide"]=self._source_slide(data,task)
                blocks=_brief(task)
                if task["artifact"]:
                    self._artifact(task,task["artifact"],check_digest=True)
                    blocks.append({"kind":"file","path":task["artifact"],"digest":task["artifact_sha256"],
                                   "caption":task["id"]+" v"+str(task["version"])})
                _notice(data,key,role,blocks,task)
                return task
            if command=="сдать":
                _require(role=="designer","Сдавать файл слайда может дизайнер.")
                ref=submission_ref(head)
                _require(ref,"Отправьте один файл с подписью «Сдать ID vN».")
                task=_ref_task(data,*ref)
                _notice(data,key,role,[_message("Приложите один готовый слайд как PNG, JPEG или WebP. "
                    f"Подпись к файлу — <code>Сдать {task['id']} v{task['version']}</code>.")],task)
                return task
            _require(role=="owner","Создавать, редактировать и принимать правки может владелец.")
            if command=="правка":
                m=re.fullmatch(r"правка\s+(.+?)\s+слайд\s+(\d+)(?:\s+(.+))?",head,re.I)
                _require(m,"Формат: Правка SKU слайд N [площадка]. Со следующей строки укажите ТЗ.")
                fields=_body(lines[1:])
                if m[3]:
                    platform=_platform(m[3])
                    _require(fields.get("platform",platform)==platform,"Указаны две разные площадки.")
                    fields["platform"]=platform
                return self._create(data,key,{"sku":m[1],"slide":int(m[2]),**fields})
            m=re.fullmatch(rf"{command}\s+{REF}",head,re.I)
            _require(m,"Скопируйте ID и текущую версию из задания, например «Принять ID v1».")
            if command=="изменить":
                return self._revise(data,key,m[1],int(m[2]),_body(lines[1:]))
            return self._decision(data,key,m[1],int(m[2]),command=="принять","\n".join(lines[1:]).strip())
        def safe_run(data,key):
            try:
                return run(data,key)
            except CardTaskError as exc:
                _notice(data,key,role,[_message(escape(str(exc)))])
                return None
        self._event("telegram:"+role,event_id,["command",text],safe_run)
        return True

    def flush(self,tg,*,designer,owner,checkpoint=None):
        """Persist each outgoing intent across runners before calling Telegram.

        An unknown Telegram outcome is isolated to its notification. A new «Задача ID»
        explicitly requests another copy without replaying an older notification.
        """
        recipients={"designer":designer,"owner":owner}
        checkpoint=checkpoint or (lambda:None)
        counts={"sent":0,"uncertain":0,"unlinked":0}
        for key in tuple(_load(self.root)["notifications"]):
            while True:
                with self._edit() as data:
                    n=data["notifications"].get(key)
                    if n is None:
                        break
                    task=data["tasks"].get(n.get("task"))
                    if task and (task["version"]!=n["version"] or (n.get("status") and task["status"]!=n["status"])):
                        del data["notifications"][key]
                        break
                    if n["state"]!="pending":
                        counts["uncertain"]+=1
                        break
                    who=recipients[n["to"]]
                    if not who:
                        counts["unlinked"]+=1
                        break
                    index=n["next"]
                    block=n["blocks"][index]
                    n["state"]="sending"
                # If the checkpoint cannot be published, Telegram has not been called.
                # Re-arm locally before propagating the infrastructure error.
                try:
                    checkpoint()
                except Exception:
                    with self._edit() as data:
                        data["notifications"][key]["state"]="pending"
                    raise
                try:
                    if block["kind"]=="file":
                        path=self.root/block["path"]
                        _require(path.resolve()==path and path.is_file(),"Файл на проверку недоступен.")
                        with path.open("rb") as f:
                            digest=sha256()
                            for chunk in iter(lambda:f.read(1024*1024),b""):
                                digest.update(chunk)
                            _require(digest.hexdigest()==block["digest"],"Файл изменился после сдачи.")
                            f.seek(0)
                            reply=tg("sendDocument",chat_id=who,caption=block["caption"],files={"document":(path.name,f)})
                    else:
                        reply=tg("sendMessage",chat_id=who,text=block["text"],parse_mode="HTML")
                    if (not isinstance(reply,dict) or type(reply.get("message_id")) is not int or reply["message_id"] <= 0
                            or ("chat" in reply and str(reply["chat"].get("id")) != str(who))):
                        raise RuntimeError("Не получена квитанция Telegram.")
                except Exception:
                    with self._edit() as data:
                        data["notifications"][key]["state"]="uncertain"
                    checkpoint()
                    counts["uncertain"]+=1
                    break
                with self._edit() as data:
                    n=data["notifications"][key]
                    n["next"]+=1
                    if n["next"]==len(n["blocks"]):
                        if n.get("assignment") and data["tasks"][n["task"]]["version"]==n["version"]:
                            data["tasks"][n["task"]]["sent_at"]=_now()
                        del data["notifications"][key]
                    else:
                        n["state"]="pending"
                counts["sent"]+=1
                checkpoint()
        return counts
