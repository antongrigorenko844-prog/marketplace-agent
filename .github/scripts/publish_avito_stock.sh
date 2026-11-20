#!/usr/bin/env bash
set -euo pipefail

stock_files=(data/orders_seen.db data/ozon_catalog.xlsx data/kits.xlsx data/avito_stock_state.json avito_stock.py avito_stock_client.py)
verify_saved_inputs() {
  git fetch --quiet --no-tags origin refs/heads/main
  if ! git diff --quiet FETCH_HEAD -- "${stock_files[@]}" ; then
    echo "::error::Складские данные или правила Авито отличаются от main. Нужен новый цикл."
    return 1
  fi
}
verify_saved_inputs
plan_dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/avito-stock.XXXXXX")
plan="$plan_dir/changes.json"
trap 'rm -f -- "$plan" "$plan.used"; rmdir -- "$plan_dir"' EXIT
python avito_stock.py prepare --plan "$plan"
bash .github/scripts/save_data.sh data/avito_stock_state.json
verify_saved_inputs
result=0
python avito_stock.py apply --plan "$plan" || result=$?
bash .github/scripts/save_data.sh data/avito_stock_state.json
exit "$result"
