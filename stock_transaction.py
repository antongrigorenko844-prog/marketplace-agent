"""Crash-recoverable projection of an order batch into the stock workbook."""

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

import openpyxl

from catalog_editor import COLUMNS, EDIT_FILL
import kits


class StockSyncError(RuntimeError):
    pass


def units(value):
    """Accept integer quantities and decimal integer strings only."""
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        return int(value.strip())
    raise StockSyncError("Количество должно быть целым числом.")


def assert_stock_ready(db_path):
    """Guard a standalone publication against an unfinished journal projection."""
    path = Path(db_path).resolve()
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)) as conn:
        pending_table = conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='stock_pending'"
        ).fetchone()
        if pending_table and conn.execute("SELECT 1 FROM stock_pending LIMIT 1").fetchone():
            raise StockSyncError("Остатки ещё не сохранены в каталог. Повторите синхронизацию заказов перед публикацией.")


def _replace_workbook(path, data):
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as fp:
            temp = Path(fp.name)
            os.fchmod(fp.fileno(), path.stat().st_mode & 0o777)
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _project_pending(conn, path):
    row = conn.execute("SELECT before_hash, workbook FROM stock_pending WHERE id=1").fetchone()
    if row is None:
        return
    before_hash, workbook = row
    current = path.read_bytes()
    if current != workbook:
        if sha256(current).hexdigest() != before_hash:
            raise StockSyncError("Каталог изменился во время незавершённого списания. Нужна сверка.")
        _replace_workbook(path, workbook)
    conn.execute("DELETE FROM stock_pending WHERE id=1")


class OrderBatch:
    def __init__(self, conn, path):
        self.conn = conn
        self.path = path
        self.original = path.read_bytes()
        self.book = openpyxl.load_workbook(BytesIO(self.original))
        qty_index = next(i for i, (key, _) in enumerate(COLUMNS) if key == "quantity_to_sell")
        self.cells = {}
        for row in self.book.active.iter_rows(min_row=2):
            if row[0].value is None:
                continue
            sku = str(row[0].value).strip()
            if sku in self.cells:
                raise StockSyncError(f"Дублирующийся SKU в каталоге: {sku}")
            self.cells[sku] = row[qty_index]
        self.bom = {}
        for kit, components in kits.load_bom(strict=True).items():
            recipe = Counter()
            for component, qty in components:
                recipe[component] += qty
            self.bom[kit] = dict(recipe)
        self.deltas = Counter()
        self.physical = Counter()
        self.summary = {"unmatched": [], "applied": {}, "kit_updates": {}}

    def reconcile(self, source, key, sku, quantity, cancelled, legacy_sku=None):
        if not key or not sku or quantity <= 0:
            raise StockSyncError("Отсутствует идентификатор заказа, артикул или положительное количество.")
        row = self.conn.execute(
            """SELECT p.offer_id, p.decremented, r.components
               FROM processed_orders p LEFT JOIN stock_order_recipe r USING(source, order_key)
               WHERE p.source=? AND p.order_key=?""", (source, key),
        ).fetchone()
        previous = row[1] if row else 0
        target = 0 if cancelled else quantity
        # Old WB records used vendorCode verbatim. An identical replay has no stock effect.
        if row and previous == target and row[0] in (sku, legacy_sku):
            return
        if row and sku != row[0]:
            raise StockSyncError(f"Изменился артикул ранее учтённой строки ({source}). Нужна сверка.")
        if previous and not row[2]:
            raise StockSyncError(f"Для изменения прежнего списания {source} / {sku} нужна сверка.")
        # On a change or cancellation use the component recipe originally booked.
        if previous:
            recipe = json.loads(row[2])
        else:
            recipe = self.bom.get(sku, {sku: 1})
        delta = previous - target
        if delta:
            if sku not in self.cells or any(c not in self.cells for c in recipe):
                raise StockSyncError(f"Неизвестный SKU товара или компонента: {sku}")
            self.deltas[sku] += delta
            for component, per_unit in recipe.items():
                if component in self.bom:
                    raise StockSyncError(f"Вложенный комплект или изменённый тип компонента: {component}")
                self.physical[component] += delta * per_unit
        self.conn.execute(
            """INSERT INTO processed_orders(source,order_key,offer_id,decremented,status,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(source,order_key) DO UPDATE SET
               decremented=excluded.decremented,status=excluded.status,updated_at=excluded.updated_at""",
            (source, key, sku, target, "cancelled" if cancelled else "active",
             datetime.now(timezone.utc).isoformat()),
        )
        if target:
            self.conn.execute("INSERT OR REPLACE INTO stock_order_recipe VALUES(?,?,?)",
                              (source, key, json.dumps(recipe, ensure_ascii=False, sort_keys=True)))

    def _level(self, sku):
        value = self.cells[sku].value
        if value is None or value == "":
            raise StockSyncError(f"Не задан остаток: {sku}")
        level = units(value)
        if level < 0:
            raise StockSyncError(f"Отрицательный исходный остаток: {sku}")
        return level

    def stage(self):
        self.summary["deltas"] = dict(self.deltas)
        applied = self.summary["applied"]
        kit_updates = self.summary["kit_updates"]
        for sku, delta in self.physical.items():
            new_level = self._level(sku) + delta
            if new_level < 0:
                raise StockSyncError(f"Недостаточный остаток для списания: {sku}")
            if delta:
                applied[sku] = new_level
        for sku, recipe in self.bom.items():
            if sku not in self.cells or (sku not in self.deltas and not applied.keys() & recipe.keys()):
                continue
            if any(c not in self.cells or c in self.bom for c in recipe):
                raise StockSyncError(f"Неполный или вложенный состав комплекта: {sku}")
            possible = min((applied[c] if c in applied else self._level(c)) // qty for c, qty in recipe.items())
            if self._level(sku) != possible:
                kit_updates[sku] = possible
        if self.path.read_bytes() != self.original:
            raise StockSyncError("Каталог изменился во время расчёта. Повторите после сверки.")
        if not applied and not kit_updates:
            return
        for sku, value in {**applied, **kit_updates}.items():
            self.cells[sku].value = value
            self.cells[sku].fill = EDIT_FILL
        buffer = BytesIO()
        self.book.save(buffer)
        self.conn.execute("INSERT INTO stock_pending VALUES(1,?,?)",
                          (sha256(self.original).hexdigest(), buffer.getvalue()))


@contextmanager
def order_batch(db_path, catalog_path, ensure_db):
    """The committed ledger always contains a replayable workbook until replacement succeeds."""
    path = Path(catalog_path)
    if not path.is_file():
        raise FileNotFoundError(f"Нет каталога: {path}")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        ensure_db(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS stock_pending(
                     id INTEGER PRIMARY KEY CHECK(id=1), before_hash TEXT NOT NULL, workbook BLOB NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS stock_order_recipe(
                     source TEXT NOT NULL, order_key TEXT NOT NULL, components TEXT NOT NULL,
                     PRIMARY KEY(source,order_key))""")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _project_pending(conn, path)
            batch = OrderBatch(conn, path)
            try:
                yield batch
                batch.stage()
            finally:
                batch.book.close()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _project_pending(conn, path)
