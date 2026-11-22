"""Read-only diagnostics for legacy order-debit recipe gaps. Never modify inventory."""
import json, sqlite3
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"data/orders_seen.db"
OUT=ROOT/"data/stock_recipe_gap_audit_20261123_result.json"
res={"read_only":True,"source":"repository snapshot, not live Ozon API","checked_utc":datetime.now(timezone.utc).isoformat(),"db_exists":DB.exists(),"targets":{}}
targets=["0cw0amdsgdq200","tolkateli-4-salniki-4"]
if DB.exists():
    with sqlite3.connect("file:"+str(DB)+"?mode=ro",uri=True,timeout=30) as con:
        con.row_factory=sqlite3.Row
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        res["tables_present"]={t:(t in tables) for t in ["processed_orders","stock_order_recipe","stock_pending","stock_deferred_release"]}
        if {"processed_orders","stock_order_recipe"}.issubset(tables):
            res["total_records"]=con.execute("SELECT COUNT(*) FROM processed_orders").fetchone()[0]
            summary=con.execute("""
              SELECT p.source,
                     COUNT(*) AS rows,
                     SUM(CASE WHEN p.decremented > 0 THEN 1 ELSE 0 END) AS positive_debits,
                     SUM(CASE WHEN p.decremented > 0 AND r.components IS NULL THEN 1 ELSE 0 END) AS positive_missing_recipes,
                     SUM(CASE WHEN p.decremented > 0 AND r.components IS NOT NULL THEN 1 ELSE 0 END) AS positive_with_recipes
                FROM processed_orders p
                LEFT JOIN stock_order_recipe r ON p.source=r.source AND p.order_key=r.order_key
               GROUP BY p.source
               ORDER BY p.source
            """).fetchall()
            res["by_source"]=[dict(x) for x in summary]
            for sku in targets:
                rows=con.execute("""
                   SELECT p.source,COUNT(*) AS records,
                     SUM(CASE WHEN p.decremented>0 AND r.components IS NULL THEN 1 ELSE 0 END) AS positive_missing_recipes,
                     SUM(CASE WHEN p.decremented>0 THEN 1 ELSE 0 END) AS positive_debits,
                     MAX(p.updated_at) AS newest_update
                   FROM processed_orders p
                   LEFT JOIN stock_order_recipe r ON p.source=r.source AND p.order_key=r.order_key
                   WHERE p.offer_id=? GROUP BY p.source ORDER BY p.source
                """,(sku,)).fetchall()
                res["targets"][sku]=[dict(x) for x in rows]
            res["top_legacy_skus"]=[dict(x) for x in con.execute("""
                SELECT p.source,p.offer_id AS sku,COUNT(*) AS missing_active_recipes
                  FROM processed_orders p
                  LEFT JOIN stock_order_recipe r ON p.source=r.source AND p.order_key=r.order_key
                 WHERE p.decremented>0 AND r.components IS NULL
                 GROUP BY p.source,p.offer_id ORDER BY missing_active_recipes DESC LIMIT 20
            """).fetchall()]
        if "stock_pending" in tables:
            res["unfinished_projection_rows"]=con.execute("SELECT COUNT(*) FROM stock_pending").fetchone()[0]
else:
    res["error"]="No repository DB; no inference about live inventory"
OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(res,ensure_ascii=False),flush=True)
