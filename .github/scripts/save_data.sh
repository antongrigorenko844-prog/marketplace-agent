#!/usr/bin/env bash
# Сохранить результаты робота (data/ и т.п.) обратно в репозиторий,
# НЕ затирая правки людей.
#
# Зачем: команда идёт несколько минут. Если за это время владелец (или Claude)
# запушил правку, например цену в data/master_control.xlsx, раньше робот делал
# `git pull --rebase -X theirs` — «при конфликте моя версия главнее». Мастер —
# двоичный файл, конфликт решался целиком, и старая копия робота затирала
# свежую правку человека (так 27.10 пропала одобренная цена 0am325025ae 4850).
#
# Теперь правило простое:
#   * файл, который за время прогона менял ЧЕЛОВЕК (любой коммит не от
#     marketplace-agent-bot) — остаётся версия человека, изменения робота
#     в этом файле отбрасываются (следующий прогон пересчитает их сам);
#   * остальные файлы — версия робота, как и раньше.
#
# Использование: bash .github/scripts/save_data.sh data/ photos/ social/
set -euo pipefail
BOT="marketplace-agent-bot"
git config user.name "$BOT"
git config user.email "actions@users.noreply.github.com"

START=$(git rev-parse HEAD)          # с какой версии робот начинал
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = "HEAD" ] && BRANCH="${GITHUB_REF_NAME:-main}"

git add -A -- "$@"
if git diff --cached --quiet; then
  echo "Нечего сохранять."
  exit 0
fi
git commit -q -m "chore: update data [skip ci]"
SNAP=$(git rev-parse HEAD)           # снимок результатов робота

for i in 1 2 3 4 5; do
  if git push -q origin "HEAD:$BRANCH"; then
    echo "Сохранено."
    exit 0
  fi
  echo "Пока шла команда, в репозиторий запушили изменения — объединяю (попытка $i)."
  git fetch -q origin "$BRANCH" || { sleep 5; continue; }
  REMOTE=$(git rev-parse FETCH_HEAD)

  # A catalog and its journal describe one inventory state. Retrying against a
  # fresh checkout is safer than combining one half of each revision.
  inventory_files=(data/orders_seen.db data/ozon_catalog.xlsx data/kits.xlsx)
  if ! git diff --quiet "$START" "$SNAP" -- "${inventory_files[@]}" &&
     ! git diff --quiet "$START" "$REMOTE" -- "${inventory_files[@]}"; then
    echo "::error::Склад или состав комплектов изменён одновременно. Нужен повтор со свежей версии."
    exit 1
  fi

  # Файлы, которые за время прогона трогал человек
  HUMAN_FILES=$(
    git log --format='%H%x09%an' "$START..$REMOTE" | while IFS=$'\t' read -r h an; do
      [ "$an" = "$BOT" ] && continue
      git -c core.quotePath=false show --format= --name-only "$h"
    done | sort -u
  )

  git reset -q --hard "$REMOTE"
  while IFS= read -r -d '' f; do
    if printf '%s\n' "$HUMAN_FILES" | grep -qxF "$f"; then
      echo "  оставляю правку человека: $f (изменения робота в нём отброшены)"
      continue
    fi
    if git cat-file -e "$SNAP:$f" 2>/dev/null; then
      git checkout -q "$SNAP" -- "$f"
    else
      git rm -q --ignore-unmatch -- "$f"
    fi
  done < <(git diff --name-only -z "$START" "$SNAP")
  git add -A -- "$@"
  if git diff --cached --quiet; then
    echo "После объединения сохранять нечего."
    exit 0
  fi
  git commit -q -m "chore: update data [skip ci]"
  START="$REMOTE"
  SNAP=$(git rev-parse HEAD)
  sleep 3
done
echo "::error::Не удалось сохранить data/ после 5 попыток — результаты этого прогона не записаны."
exit 1
