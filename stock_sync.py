"""
Единый учёт остатков между Ozon, Wildberries (и в будущем — Avito).

Идея: "Кол-во к продаже" в data/ozon_catalog.xlsx — ЕДИНСТВЕННЫЙ источник
истины по остатку. Этот модуль:
  1. Забирает заказы за период с Ozon (FBS + FBO) и WB (Statistics API,
     полная история — не только новые/неподтверждённые).
  2. Для каждой строки заказа определяет offer_id (у Ozon и WB это ваш
     собственный артикул продавца — сопоставление автоматическое, без
     ручного подтверждения, в отличие от Avito) и списывает 1 (или
     quantity, если поле есть) со склада в xlsx.
  3. Если заказ был учтён, а потом отменён — остаток возвращается обратно
     (двойного списания/начисления не бывает).
  4. Чтобы не задвоить обработку при повторных запусках, каждая строка
     заказа помечается обработанной в data/orders_seen.db (sqlite,
     сохраняется в репозиторий тем же workflow, что и остальные data/*).

Avito ПОКА НЕ включён сюда — сопоставление объявление->артикул там требует
ручного подтверждения человеком (data/avito_item_map.json), а такой карты
для новых объявлений ещё нет (старые 37 решили не трогать). Как только
появится подтверждённая карта, это будет отдельным шагом — структура here
уже это учитывает (_reconcile — источник-агностичный).

ВАЖНО: этот модуль только СЧИТАЕТ и правит data/ozon_catalog.xlsx локально.
Рассылку обновлённого остатка обратно в Ozon/WB (update_stocks) он пока НЕ
делает — это следующий отдельный шаг (--push-stock), чтобы сначала
убедиться, что подсчёт корректный (см. README/чат).
"""
import datetime
import logging
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from config import config

logger = logging.getLogger("marketplace-agent.stock_sync")

DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "ozon_catalog.xlsx")

_OZON_CANCELLED_STATUSES = {"cancelled", "not_accepted"}


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_orders (
            source TEXT NOT NULL,
            order_key TEXT NOT NULL,
            offer_id TEXT,
            decremented INTEGER NOT NULL DEFAULT 0,
            status TEXT,
            updated_at TEXT,
            PRIMARY KEY (source, order_key)
        )
        """
    )
    conn.commit()


def _reconcile_line(
    conn: sqlite3.Connection,
    source: str,
    order_key: str,
    offer_id: str,
    quantity: int,
    is_cancelled: bool,
    deltas: Dict[str, int],
) -> None:
    """
    Один заказ/одна строка заказа. Идемпотентно: повторный вызов с теми же
    (source, order_key) и тем же статусом ничего не меняет; смена статуса
    на "отменён" возвращает ранее списанное; смена с "отменён" на активный
    списывает заново.
    """
    if not offer_id or quantity <= 0:
        return

    row = conn.execute(
        "SELECT decremented, status FROM processed_orders WHERE source = ? AND order_key = ?",
        (source, order_key),
    ).fetchone()

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if row is None:
        if is_cancelled:
            conn.execute(
                "INSERT INTO processed_orders (source, order_key, offer_id, decremented, status, updated_at) "
                "VALUES (?, ?, ?, 0, 'cancelled', ?)",
                (source, order_key, offer_id, now),
            )
        else:
            deltas[offer_id] = deltas.get(offer_id, 0) - quantity
            conn.execute(
                "INSERT INTO processed_orders (source, order_key, offer_id, decremented, status, updated_at) "
                "VALUES (?, ?, ?, ?, 'active', ?)",
                (source, order_key, offer_id, quantity, now),
            )
        return

    prev_decremented, _prev_status = row
    if is_cancelled and prev_decremented > 0:
        # Заказ был учтён (остаток списан), теперь отменён — вернуть обратно.
        deltas[offer_id] = deltas.get(offer_id, 0) + prev_decremented
        conn.execute(
            "UPDATE processed_orders SET decremented = 0, status = 'cancelled', updated_at = ? "
            "WHERE source = ? AND order_key = ?",
            (now, source, order_key),
        )
    elif not is_cancelled and prev_decremented == 0:
        # Раньше был отменён/не учтён, теперь снова активен — списать.
        deltas[offer_id] = deltas.get(offer_id, 0) - quantity
        conn.execute(
            "UPDATE processed_orders SET decremented = ?, status = 'active', updated_at = ? "
            "WHERE source = ? AND order_key = ?",
            (quantity, now, source, order_key),
        )
    # Иначе — статус не изменился относительно того, что уже учтено, ничего не делаем.


def _ozon_is_cancelled(status: str) -> bool:
    return str(status or "").strip().lower() in _OZON_CANCELLED_STATUSES


def _sync_ozon_fbs(conn: sqlite3.Connection, since_iso: str, to_iso: str, deltas: Dict[str, int]) -> int:
    import ozon_client

    postings = ozon_client.get_fbs_orders_since(since_iso, to_iso)
    count = 0
    for posting in postings:
        posting_number = posting.get("posting_number") or ""
        cancelled = _ozon_is_cancelled(posting.get("status"))
        for product in posting.get("products", []) or []:
            offer_id = str(product.get("offer_id") or "").strip()
            quantity = int(product.get("quantity") or 0)
            order_key = f"{posting_number}:{offer_id}"
            _reconcile_line(conn, "ozon_fbs", order_key, offer_id, quantity, cancelled, deltas)
            count += 1
    return count


def _sync_ozon_fbo(conn: sqlite3.Connection, since_iso: str, to_iso: str, deltas: Dict[str, int]) -> int:
    import ozon_client

    postings = ozon_client.get_fbo_orders_since(since_iso, to_iso)
    count = 0
    for posting in postings:
        posting_number = posting.get("posting_number") or ""
        cancelled = _ozon_is_cancelled(posting.get("status"))
        for product in posting.get("products", []) or []:
            offer_id = str(product.get("offer_id") or "").strip()
            quantity = int(product.get("quantity") or 0)
            order_key = f"{posting_number}:{offer_id}"
            _reconcile_line(conn, "ozon_fbo", order_key, offer_id, quantity, cancelled, deltas)
            count += 1
    return count


def _sync_wb(conn: sqlite3.Connection, date_from_iso: str, deltas: Dict[str, int]) -> int:
    import wb_client

    rows = wb_client.get_orders_since(date_from_iso)
    count = 0
    for row in rows:
        offer_id = str(row.get("supplierArticle") or "").strip()
        order_key = str(row.get("srid") or row.get("odid") or row.get("gNumber") or "")
        if not order_key:
            continue
        cancelled = bool(row.get("isCancel"))
        # У WB Statistics API одна строка = обычно одна единица товара; если
        # вдруг появится поле quantity — используем его, иначе считаем 1.
        try:
            quantity = int(row.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        _reconcile_line(conn, "wb", order_key, offer_id, quantity, cancelled, deltas)
        count += 1
    return count


def sync_all_orders(
    days_back: int = 30,
    catalog_path: str = DEFAULT_CATALOG_PATH,
    db_path: Optional[str] = None,
) -> dict:
    """
    Основной вход. Возвращает сводку:
    {"ozon_fbs": N, "ozon_fbo": N, "wb": N, "deltas": {offer_id: -k, ...},
     "applied": {offer_id: новый_остаток, ...}, "unmatched": [...]}
    """
    import catalog_editor

    if not os.path.exists(catalog_path):
        raise FileNotFoundError(
            f"Нет файла {catalog_path} — сначала выполните fetch-ozon и build-ozon-catalog."
        )

    db_path = db_path or config.db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    _ensure_db(conn)

    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=days_back)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    to_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    wb_date_from = since.strftime("%Y-%m-%d")

    deltas: Dict[str, int] = {}
    summary: Dict[str, object] = {}

    try:
        summary["ozon_fbs"] = _sync_ozon_fbs(conn, since_iso, to_iso, deltas)
    except Exception as exc:
        logger.error("Ozon FBS: ошибка при получении/разборе заказов: %s", exc)
        summary["ozon_fbs_error"] = str(exc)

    try:
        summary["ozon_fbo"] = _sync_ozon_fbo(conn, since_iso, to_iso, deltas)
    except Exception as exc:
        logger.error("Ozon FBO: ошибка при получении/разборе заказов: %s", exc)
        summary["ozon_fbo_error"] = str(exc)

    try:
        summary["wb"] = _sync_wb(conn, wb_date_from, deltas)
    except Exception as exc:
        logger.error("WB: ошибка при получении/разборе заказов: %s", exc)
        summary["wb_error"] = str(exc)

    conn.commit()
    conn.close()

    summary["deltas"] = dict(deltas)
    if deltas:
        applied, unmatched = catalog_editor.apply_stock_deltas(catalog_path, deltas)
        summary["applied"] = applied
        summary["unmatched"] = unmatched
    else:
        summary["applied"] = {}
        summary["unmatched"] = []

    return summary
