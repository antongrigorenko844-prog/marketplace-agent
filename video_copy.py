"""Prepare platform copy from reviewed media, product mappings and local Wordstat data."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re


CATALOG = "https://leoparts-akpp.ru/catalog"
PLATFORMS = ("YouTube", "RuTube", "TikTok")
MANIFEST = Path("data/designer_video_copy.json")
REVIEW = "Проверьте кадры и артикулы, затем добавьте или обновите запись в data/designer_video_copy.json."


def _json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _clip(entry, root):
    articles = entry.get("articles")
    filename = entry.get("file")
    if (not isinstance(articles,list) or not articles or not all(isinstance(a,str) and a for a in articles)
            or not isinstance(filename,str)):
        raise ValueError("У ролика нет точной привязки к карточкам. "+REVIEW)
    path = PurePosixPath(filename)
    if (path.as_posix()!=filename or ".." in path.parts or len(path.parts)<3 or
            path.parts[0]!="photos" or path.suffix.lower()!=".mp4"):
        raise ValueError("Неизвестный путь к видео. "+REVIEW)
    if not (root/MANIFEST).is_file():
        raise ValueError("В репозитории отсутствует описание видео. "+REVIEW)
    manifest = _json(root/MANIFEST)
    if manifest.get("version")!=1 or not isinstance(manifest.get("clips"),dict):
        raise ValueError("Неизвестная версия описания видео. "+REVIEW)
    clip = manifest["clips"].get(filename)
    if not isinstance(clip,dict) or clip.get("articles")!=articles:
        raise ValueError("Для файла или его состава карточек требуется проверка. "+REVIEW)
    if not clip.get("visual_fact") or not re.fullmatch(r"[0-9a-f]{64}",str(clip.get("sha256"))):
        raise ValueError("У видео нет проверенного описания кадра или SHA-256. "+REVIEW)
    return clip


def _terms(clip, root):
    cache = _json(root/"data/wordstat_cache.json")
    evidence = []
    for term in clip.get("keywords",[]):
        phrase = term.get("phrase")
        if not isinstance(phrase,str) or not phrase.strip() or phrase!=phrase.strip().casefold():
            raise ValueError("Для ролика указана некорректная ключевая фраза. "+REVIEW)
        key = phrase+"|225|DEVICE_ALL"
        record = cache.get(key,{})
        count = record.get("totalCount")
        if count is None or not str(count).isascii() or not str(count).isdigit():
            raise ValueError("Для проверенной фразы нет частотности Wordstat. "+REVIEW)
        if (type(term.get("sheet_row")) is not int or term["sheet_row"]<2 or
                type(term.get("count")) is not int or term["count"]<0 or
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}",str(term.get("added")))):
            raise ValueError("Нет проверяемого источника для ключевой фразы. "+REVIEW)
        evidence.append({"phrase":phrase,"count":int(count),"cache_key":key,
                         "reviewed_count":term["count"],"sheet_row":term["sheet_row"],"added":term["added"]})
    if not evidence:
        raise ValueError("Не выбраны тематические фразы из Wordstat. "+REVIEW)
    return sorted(evidence,key=lambda t:-t["count"])


def keyword_evidence(entry, root=Path(".")):
    """Dated workbook evidence and current cached search counts for Russia/all devices."""
    root=Path(root)
    return _terms(_clip(entry,root),root)


def build_copy(entry, root=Path(".")):
    """Return YouTube, RuTube, TikTok sections, each containing title, description, hashtags."""
    root=Path(root)
    clip=_clip(entry,root)
    digest=sha256()
    with (root/entry["file"]).open("rb") as media:
        for chunk in iter(lambda:media.read(1024*1024),b""):
            digest.update(chunk)
    if digest.hexdigest()!=clip["sha256"]:
        raise ValueError("Содержимое ролика изменилось. "+REVIEW)
    terms=_terms(clip,root)
    oem=next((t["phrase"].upper() for t in terms if re.fullmatch(r"0am[a-z0-9]+",t["phrase"])),None)
    tags=clip.get("tags")
    if not isinstance(tags,list) or not tags or not all(isinstance(t,str) and t.isalnum() for t in tags):
        raise ValueError("Нужно проверить тематические хэштеги. "+REVIEW)
    templates=clip.get("platforms",{})
    if set(templates)!=set(PLATFORMS):
        raise ValueError("Нужны отдельные тексты для YouTube, RuTube и TikTok. "+REVIEW)
    result={}
    for platform in PLATFORMS:
        title,desc=templates[platform].get("title"),templates[platform].get("description")
        if not isinstance(title,str) or not 1<=len(title.strip())<=100 or not isinstance(desc,str):
            raise ValueError("Нужны проверенные заголовок и описание. "+REVIEW)
        if not any(term["phrase"] in desc.casefold() for term in terms):
            raise ValueError("В описании для "+platform+" нет проверенной тематической фразы. "+REVIEW)
        selected=("LEOPARTS",*tags) if platform=="YouTube" else ("LEOPARTS",tags[0],tags[-1])
        if oem and platform=="RuTube":
            selected=(*selected,oem)
        if platform!="TikTok":
            desc+="\n\nКаталог LEOPARTS — "+CATALOG
        hashtags=" ".join("#"+tag for tag in dict.fromkeys(selected))
        if len(desc)>600 or len(hashtags)>150:
            raise ValueError("Текст для "+platform+" превышает размер подготовленного блока. "+REVIEW)
        result[platform]={"title":title,"description":desc,"hashtags":hashtags}
    return result
