"""Read-only FBS deadline report. Never ships orders or changes prices/stocks."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
PENDING = {"awaiting_packaging", "awaiting_deliver"}


def parse_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None and result.year > 1970 else None
    except (ValueError, TypeError):
        return None


def report(postings, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("The report time must include its timezone")
    rows = []
    for posting in postings:
        if posting.get("status") not in PENDING:
            continue
        # shipment_date can be the recommended slot; it is not a safe fallback.
        deadline = parse_time(posting.get("shipment_date_without_delay"))
        if deadline is None:
            urgency = "Срок не получен — проверить в кабинете"
        elif deadline <= now:
            urgency = "Срок уже наступил — проверить приёмку"
        elif deadline - now <= timedelta(hours=3):
            urgency = "До срока не больше 3 часов"
        elif deadline.astimezone(MOSCOW).date() == now.astimezone(MOSCOW).date():
            urgency = "Сдать сегодня"
        else:
            urgency = "Предстоящая отгрузка"
        rows.append({"posting_number": posting["posting_number"], "status": posting["status"],
                     "deadline_utc": deadline.isoformat() if deadline else None,
                     "deadline_moscow": deadline.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M") if deadline else None,
                     "urgency": urgency,
                     "warehouse": (posting.get("delivery_method") or {}).get("warehouse"),
                     "products": [{"offer_id": p.get("offer_id"), "quantity": p.get("quantity")}
                                  for p in posting.get("products", [])]})
    rows.sort(key=lambda x: x["deadline_utc"] or "")
    return {"generated_at": now.isoformat(), "timezone": "Europe/Moscow", "pending_count": len(rows), "rows": rows}


def markdown(data):
    lines = ["Ozon — ожидающие отгрузки", "", "Все сроки ниже — по Москве (МСК, UTC+3).",
             "Источник срока: shipment_date_without_delay. Рекомендуемый слот не подменяет крайний срок.",
             "Статус «Готов к отгрузке» не подтверждает фактическую приёмку пунктом.", "",
             "| Отправление | Крайний срок МСК | Состояние | Товары |", "|---|---|---|---|"]
    for row in data["rows"]:
        products = "; ".join(f"{p['offer_id']} × {p['quantity']}" for p in row["products"])
        lines.append(f"| {row['posting_number']} | {row['deadline_moscow'] or 'не получен'} | {row['urgency']} | {products} |")
    if not data["rows"]:
        lines += ["", "В проверенной выборке ожидающих отгрузки нет."]
    lines += ["", "После сдачи сверить список с актом приёмки и статусами в кабинете. Если передали вовремя, а отметка запоздала — сохранить акт и оспаривать конкретные отправления.",
              "Смена настроек метода не подтверждает перенос сроков уже полученных заказов: проверять их отдельно.",
              "", f"Снимок: {data['generated_at']}"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/tmp/fbs-dispatch-audit"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.input:
        postings = json.loads(args.input.read_text())
    else:
        import ozon_client
        postings = ozon_client.get_fbs_orders_since((now - timedelta(days=90)).isoformat(), now.isoformat())
    data = report(postings, now)
    data["scope"] = "Provided snapshot" if args.input else "Orders received in the last 90 days"
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dispatch.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    (args.output / "dispatch.md").write_text(markdown(data))
    print(markdown(data))


if __name__ == "__main__":
    main()
