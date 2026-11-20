"""Reconcile Ozon FBS/FBO, WB, Yandex Market and Tilda orders against the shared catalog.

Avito orders require a confirmed SKU mapping and have no automatic debit here.
Publishing the updated catalog is a separate command. The ledger/projection
transaction and crash recovery live in stock_transaction.py.
"""
import datetime
import os
import sqlite3
from typing import Dict, List, Optional

from config import config
from stock_transaction import StockSyncError, order_batch, units

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


def _item_quantities(items, sku_field, qty_field):
    if not items:
        raise StockSyncError("Заказ получен без товарных позиций. Нужна сверка.")
    quantities = {}
    for item in items:
        sku = str(item.get(sku_field) or "").strip()
        qty = units(item.get(qty_field))
        if not sku or qty <= 0:
            raise StockSyncError("Строка заказа без артикула или с неположительным количеством.")
        quantities[sku] = quantities.get(sku, 0) + qty
    return quantities


def _sync_ozon(batch, postings, source):
    count = 0
    for posting in postings:
        number = str(posting.get("posting_number") or "").strip()
        if not number:
            raise StockSyncError(f"Отсутствует номер отправления {source}.")
        status = str(posting.get("status") or "").strip().lower()
        if not status:
            raise StockSyncError(f"Отсутствует статус отправления {source}.")
        cancelled = status in _OZON_CANCELLED_STATUSES
        quantities = _item_quantities(posting.get("products") or [], "offer_id", "quantity")
        for sku, qty in quantities.items():
            batch.reconcile(source, f"{number}:{sku}", sku, qty, cancelled)
            count += 1
    return count


def _sync_wb(batch, date_from_iso):
    import wb_client
    import sync_master_control

    vendor_to_offer = {vendor: offer for offer, vendor in sync_master_control._WB_FORCED_OZON_TO_VENDOR.items()}
    rows = wb_client.get_orders_since(date_from_iso)
    for row in rows:
        vendor = str(row.get("supplierArticle") or "").strip()
        key = str(row.get("srid") or row.get("odid") or "").strip()
        qty = units(row.get("quantity", 1))
        cancelled = row.get("isCancel")
        if not isinstance(cancelled, bool):
            raise StockSyncError("WB вернул неизвестное значение признака отмены.")
        if vendor in sync_master_control.WB_RETIRED_VENDOR_CODES:
            previous = batch.conn.execute(
                "SELECT decremented FROM processed_orders WHERE source='wb' AND order_key=?", (key,)
            ).fetchone()
            if (previous[0] if previous else 0) != (0 if cancelled else qty):
                raise StockSyncError(f"Движение по снятой карточке WB {vendor}: нужна сверка артикула.")
        sku = vendor_to_offer.get(vendor, vendor)
        batch.reconcile("wb", key, sku, qty, cancelled, legacy_sku=vendor)
    return len(rows)


def _sync_yandex(batch):
    import yandex_market_client

    conn = batch.conn
    first_run = not conn.execute(
        "SELECT 1 FROM processed_orders WHERE source='yandex_market' AND order_key='__baseline_done__'"
    ).fetchone()
    variants = {}
    for sku in batch.cells:
        variants.setdefault(sku.casefold(), []).append(sku)
    count = 0
    for order in yandex_market_client.get_orders_since(days_back=29):
        order_id = order.get("id")
        if order_id is None:
            raise StockSyncError("Маркет вернул заказ без номера.")
        status = str(order.get("status") or "").strip().upper()
        if not status:
            raise StockSyncError("Отсутствует статус заказа Маркета.")
        cancelled = status == "CANCELLED"
        for offer, qty in _item_quantities(order.get("items") or [], "offerId", "count").items():
            key = f"{order_id}:{offer}"
            seen = conn.execute("SELECT status FROM processed_orders WHERE source='yandex_market' AND order_key=?",
                                (key,)).fetchone()
            if seen and seen[0] == "baseline":
                continue
            if first_run and not seen:
                conn.execute("INSERT INTO processed_orders VALUES('yandex_market',?,?,0,'baseline',?)",
                             (key, offer, datetime.datetime.now(datetime.timezone.utc).isoformat()))
            else:
                matches = variants.get(offer.casefold(), [])
                sku = offer if offer in batch.cells or len(matches) != 1 else matches[0]
                batch.reconcile("yandex_market", key, sku, qty, cancelled)
            count += 1
    if first_run:
        conn.execute("INSERT INTO processed_orders VALUES('yandex_market','__baseline_done__','',0,'marker',?)",
                     (datetime.datetime.now(datetime.timezone.utc).isoformat(),))
    return count


def sync_all_orders(
    days_back: int = 30,
    catalog_path: str = DEFAULT_CATALOG_PATH,
    db_path: Optional[str] = None,
) -> dict:
    """Apply one complete poll; a failed source or validation rolls the batch back."""
    import ozon_client
    import yandex_market_client

    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=days_back)
    since_iso = since.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    to_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with order_batch(db_path or config.db_path, catalog_path, _ensure_db) as batch:
        summary = batch.summary
        summary["ozon_fbs"] = _sync_ozon(batch, ozon_client.get_fbs_orders_since(since_iso, to_iso), "ozon_fbs")
        summary["ozon_fbo"] = _sync_ozon(batch, ozon_client.get_fbo_orders_since(since_iso, to_iso), "ozon_fbo")
        summary["wb"] = _sync_wb(batch, since.date().isoformat())
        if yandex_market_client.is_configured():
            summary["yandex_market"] = _sync_yandex(batch)
    return summary


def sync_tilda_order(
    order_key: str,
    items: List[Dict[str, object]],
    catalog_path: str = DEFAULT_CATALOG_PATH,
    db_path: Optional[str] = None,
) -> dict:
    """Reconcile one Tilda order, including repeated lines of the same SKU."""
    if not order_key or not order_key.strip() or not items:
        raise StockSyncError("Требуются номер заказа и непустой список позиций Тильды.")
    normalized = [{"offer_id": it.get("offer_id"), "qty": it.get("qty", it.get("quantity"))} for it in items]
    quantities = _item_quantities(normalized, "offer_id", "qty")
    with order_batch(db_path or config.db_path, catalog_path, _ensure_db) as batch:
        for sku, qty in quantities.items():
            batch.reconcile("tilda", f"{order_key}:{sku}", sku, qty, False)
        batch.summary.update(items=len(items), skipped=[])
    return batch.summary
