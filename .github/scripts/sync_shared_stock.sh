#!/usr/bin/env bash
set -euo pipefail

python main.py --sync-orders
bash .github/scripts/save_data.sh data/
failed=0
bash .github/scripts/publish_avito_stock.sh || failed=1
python main.py --push-stock || failed=1
python main.py --push-wb-stock || failed=1
if [ -n "${YANDEX_MARKET_API_KEY:-}" ]; then
  python main.py --push-yandex-stock || failed=1
fi
python main.py --build-avito-catalog || failed=1
if [ -n "${TILDA_COMMERCEML_URL:-}" ]; then
  python main.py --push-tilda || failed=1
fi
exit "$failed"
