"""Read existing semantic XLSX/TSV/JSON sources without modifying them.

Only Python standard library. No workbook formulas are evaluated, no network or
advertising calls, no source-file saves. Dates are retained only as source data.
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

OUT = Path("data/direct_keywords_semantics_source_20261120.json")
SOURCES = [Path(x) for x in (
    "seo_keywords.xlsx", "data/wordstat_queue.xlsx", "data/direct/wordstat_2026-10-28.xlsx",
    "data/direct/dq200_site.tsv", "data/direct/dq200_landing.tsv", "data/direct/dq200_errors.tsv")]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_FILE = 32 * 1024 * 1024
SENSITIVE = re.compile(r"^(?:access[_-]?token|refresh[_-]?token|token|api[_-]?key|password|secret|authorization|cookie|client[_-]?login)$", re.I)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def xml(raw):
    if len(raw) > MAX_FILE or b"<!ENTITY" in raw.upper() or b"\x00" in raw:
        raise RuntimeError("Unsafe or oversized XML member")
    return ET.fromstring(raw)


def scrub(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if SENSITIVE.fullmatch(str(key)) else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def column_number(reference):
    match = re.match(r"([A-Z]+)", reference)
    if not match:
        raise RuntimeError("Invalid XLSX cell reference")
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    if not 1 <= number <= 16384:
        raise RuntimeError("XLSX column out of bounds")
    return number


def workbook(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 128 * 1024 * 1024:
            raise RuntimeError("XLSX uncompressed size exceeds 128 MiB")
        def read(name):
            info = archive.getinfo(name)
            if info.file_size > MAX_FILE:
                raise RuntimeError("XLSX member exceeds 32 MiB")
            return archive.read(name)
        names = set(archive.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in item.iter(NS + "t"))
                      for item in xml(read("xl/sharedStrings.xml")).findall(NS + "si")]
        book = xml(read("xl/workbook.xml"))
        relations = xml(read("xl/_rels/workbook.xml.rels"))
        rels = {item.get("Id"): item.get("Target") for item in relations.findall(REL_NS + "Relationship")
                if item.get("TargetMode") != "External"}
        result = {"sheets": [], "formula_evaluation": False, "numeric_cells_preserved_as_strings": True,
                  "workbook_properties": {}, "statistic_refresh_date": None,
                  "date_note": "Workbook properties/filename dates are source metadata, not proof of Wordstat query date."}
        if "docProps/core.xml" in names:
            props = xml(read("docProps/core.xml"))
            for child in props:
                key = child.tag.rsplit("}", 1)[-1]
                if key in {"created", "modified", "lastPrinted", "revision"}:
                    result["workbook_properties"][key] = child.text
        props = book.find(NS + "workbookPr")
        result["excel_date1904"] = props.get("date1904", "0") if props is not None else "0"
        cell_total = 0
        for sheet in book.findall(NS + "sheets/" + NS + "sheet"):
            target = rels.get(sheet.get(RID))
            if not target:
                raise RuntimeError("Sheet relationship is missing or external")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not path.startswith("xl/") or path not in names:
                raise RuntimeError("Sheet ZIP member outside allowed xl scope")
            tree = xml(read(path))
            dimension = tree.find(NS + "dimension")
            rows = []
            xml_rows = tree.findall(NS + "sheetData/" + NS + "row")
            for index, row in enumerate(xml_rows, 1):
                values, formulas, types = {}, {}, {}
                for cell in row.findall(NS + "c"):
                    ref = cell.get("r", "")
                    col = column_number(ref)
                    kind = cell.get("t", "n")
                    v, f = cell.find(NS + "v"), cell.find(NS + "f")
                    value = v.text if v is not None else None
                    if kind == "s" and value is not None:
                        si = int(value)
                        if not 0 <= si < len(shared):
                            raise RuntimeError("Invalid shared-string index")
                        value = shared[si]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(NS + "t"))
                    if value is not None or f is not None:
                        values[col] = value
                        types[str(col)] = kind
                        if f is not None:
                            formulas[ref] = f.text or ""
                        cell_total += 1
                        if cell_total > 1500000:
                            raise RuntimeError("Workbook exceeds 1.5 million populated cells")
                if values:
                    record = {"row": int(row.get("r", index)), "values": [values.get(i) for i in range(1, max(values) + 1)],
                              "cell_types_by_column": types}
                    if formulas:
                        record["formulas_not_evaluated"] = formulas
                    rows.append(record)
            result["sheets"].append({"name": sheet.get("name"), "sheet_id": sheet.get("sheetId"),
                "state": sheet.get("state", "visible"), "declared_dimension": dimension.get("ref") if dimension is not None else None,
                "xml_row_count": len(xml_rows), "nonempty_row_count": len(rows), "header_preview": rows[:5], "rows": rows})
        result["populated_cells"] = cell_total
        return result


def export():
    report = {"mode": "read_only_local_source_export", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [], "source_files_modified": False,
        "interpretation": "These are candidate historical files, not a confirmed user working core or new Wordstat measurements. The large data/seo_keywords.xlsx and Wordstat cache are deliberately excluded. Missing timestamps remain unknown."}
    failures = 0
    for path in SOURCES:
        record = {"path": str(path), "filename_dates": re.findall(r"20\d{2}-\d{2}-\d{2}", path.name)}
        report["sources"].append(record)
        if not path.exists():
            record["status"] = "missing"
            if path in SOURCES[:3]:
                failures += 1
            continue
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_FILE:
                raise RuntimeError("Source exceeds 32 MiB")
            record.update(size_bytes=len(raw), sha256=sha(raw))
            if path.suffix == ".xlsx":
                record["workbook"] = workbook(raw)
            elif path.suffix == ".tsv":
                rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t"))
                record.update(row_count=len(rows), header_preview=rows[:5], export_mode="metadata_and_header_preview_only")
            else:
                value = json.loads(raw)
                record["data"] = scrub(value)
                record["top_level_entry_count"] = len(value) if isinstance(value, (dict, list)) else None
                if path.name == "wordstat_cache.json" and isinstance(value, dict):
                    entries = []
                    for key, item in value.items():
                        parts = key.rsplit("|", 2)
                        entries.append({"cache_key": key, "phrase": parts[0],
                            "regions_from_key": parts[1] if len(parts) == 3 else None,
                            "device_from_key": parts[2] if len(parts) == 3 else None,
                            "totalCount": item.get("totalCount") if isinstance(item, dict) else None})
                    record["wordstat_cache_index"] = entries
                    record["cache_timestamp"] = None
                    record["cache_timestamp_note"] = "No timestamp is inferred from checkout time or export time; inspect explicit source fields if present."
            if sha(path.read_bytes()) != record["sha256"]:
                raise RuntimeError("Source changed concurrently during export")
            record["status"] = "complete"
        except Exception as error:
            record["status"] = "failed"
            record["error_type"] = type(error).__name__
            failures += 1
    report["status"] = "partial" if failures else "complete"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print("Read-only semantic sources:", report["status"], "; present:", sum(x["status"] != "missing" for x in report["sources"]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(export())
