# Как в marketplace-agent устроены цены и публикация

Проверено 20 ноября 2026 года (Asia/Shanghai). Репозиторий `antongrigorenko844-prog/marketplace-agent`; исходный полный tree `2b6f17d8692559c4423c12eb222827a9ddeda28d`, 1 308 записей. Проверены 96 YAML workflow и основные модули публикации. Это чтение кода, рабочих таблиц и двух свежих журналов Actions; цены и настройки не изменялись. Новая операция аудита ключей, добавленная после снимка tree, проверена отдельно как чтение.

## Главный вывод

**В репозитории уже есть общий файл товаров, но цены фактически публикуются из нескольких источников.** `master_control.xlsx` служит редактируемым сводом; отдельная команда переносит его значения в рабочие каталоги. Сайт и Авито дополнительно используют отдельную книгу цен, а Директ получает цены из сохранённого снимка сайта. При ценовом аудите нужно сверять эти значения рядом с их источниками и датами. Нельзя считать одну колонку мастера текущей ценой покупателя на всех площадках.

## Проверенные источники

| Источник | Что он означает | Как используется |
|---|---|---|
| `data/master_control.xlsx`, лист «Товары» | 153 точных SKU, 28 колонок; 143 цены Ozon, 137 цен WB; 123 заполненных закупки | Общий редактируемый свод; `sync-master-control` переносит поля в каталоги. `build-master-control` собирает его обратно из каталогов. |
| `data/ozon_catalog.xlsx` | Рабочие значения Ozon: цена, зачёркнутая цена, остаток, контент | Источник автоматического `sync-ozon-prices`; также резервная цена для Tilda/Авито. |
| `data/wb_catalog.xlsx` | Желаемая финальная цена продавца WB | `push-wb-price` пересчитывает цену до текущей скидки. Это отдельная команда. |
| `data/avito_site_price_overrides.xlsx` | Отдельные предписанные цены Авито и сайта, фиксации и лист «Настройки» | Tilda читает канал `site`; Авито — канал по умолчанию. Если SKU отсутствует, берётся цена Ozon из каталога. |
| `data/site_prices.json` | **Наблюдаемый снимок цен публичного магазина Tilda**, а не master-прайс | Записывается `site_links.py` после чтения публичного списка товаров; читается генератором рекламного фида. |
| `feeds/dq200_candidate.yml` | Готовый рекламный фид 12 товаров | Источник действующего feed4061297. Формируется из `site_prices.json`, `site_links.json` и остатков Ozon-каталога. |

Структура мастера проверена непосредственно в `data/master_control.xlsx`. У шести из 12 рекламных SKU закупка в мастере отсутствует: E/G/K и HXE/HXG/HXK. Значения D/F нельзя автоматически распространить на них.

Сверка существующих файлов не обнаружила расхождений цен между master и Ozon-каталогом по точному SKU. Для всех 12 рекламных товаров эффективная цена сайта из книги overrides совпала с `site_prices.json`. Эти совпадения относятся к сохранённым файлам; отдельной свежей проверкой каждого кабинета они не являются.

В книге настроек сейчас: режим **«прибыль»**, налог 6%, комиссия Авито 5%, эквайринг сайта 3%, минимальная чистая прибыль — 1 закупка, не пересчитывать дорогие позиции с ценой Ozon от 20 000 ₽. Альтернативные параметры доли — 70%, для цены Ozon от 50 000 ₽ — 60%, округление 10 ₽. Это подтверждённые **настройки существующего кода**, а не проверенные тарифы площадок или гарантия рентабельности. Детальный разбор расчёта выполняется отдельно.

## Что работает автоматически

| Поток | Триггер в текущем YAML | Реальное действие и предел проверки |
|---|---|---|
| `main.yml` | Каждые 3 часа; отдельная ночная ветка; webhook Tilda; ручной запуск | Заказы/остатки Ozon, WB, Маркета, сборка и публикация Avito-фида, CommerceML Tilda. Cron включает **цены и каталог сайта/Авито**, несмотря на назначение «синхронизация остатков». |
| `sync-prices.yml` | `0 5,17 * * *` и ручной запуск | Сверяет Ozon с `ozon_catalog.xlsx`, отправляет отличающиеся `price`/`old_price`; `no_price_sync` исключает SKU. Прямые изменения цены в кабинете могут быть возвращены к каталогу. |
| `auto-update-cards.yml` → `content-sync.yml` | Push мастера, источников контента, медиа и трёх модулей контента | Режим берётся из запроса `inspect/apply/verify`. Запись ограничена контентом; HTTP-граница запрещает цены, скидки, остатки, неизвестные методы. Сама эта цепочка **не синхронизирует новые цены мастера**. |
| `dq200-ad-feed.yml` | Ручной запуск либо push генератора/самого workflow | Пересобирает два YML и stock audit. Изменение `site_prices.json`, каталога или книги цен само по себе не является его триггером. |
| `claude-run.yml` | Push `data/run_request.txt` | Диспетчер фиксированного списка команд. Название readonly не означает отсутствие любых записей: есть создание черновика Direct, утверждённые атрибуты Ozon и локальные обновления отчётов/мастера. |
| Остальные постоянные задания | Designer, designer-video, Direct daily report, kit-slide-publish, ozon-unit | Контент/сообщения/отчёты; не общий синхронизатор цен. Отдельные ручные и marker workflow не запускаются от произвольной правки прайса. |

В снимке 96 workflow: 7 имеют расписание, 58 допускают только ручной dispatch, 31 имеют push-триггер; категории пересекаются. Точные правила запуска находятся в исходных YAML каталога `.github/workflows/`. Для исторических разовых скриптов проверены YAML-триггеры и точки входа; вся их бизнес-логика не объявляется повторно проверенной.

**Факт выполнения 19 ноября:** main run44512261963 сообщил сборку 124 Avito-позиций, публикацию файла в Release и завершённый `push-tilda` в 11:10 UTC. Sync-prices run44540016178 в 14:05 UTC сообщил 0 расхождений цены, 0 отправок, 0 ошибок. Это подтверждает выполнение цепочек; лог не заменяет проверку отображения каждой цены в кабинете/на сайте. Общий зелёный main также не гарантирует успех каждого подшага: часть команд выполняется с `|| true`.

## Подтверждённые границы и риски для ценового аудита

1. **Рекламный фид обновляется отдельной цепочкой.** Книга цен → CommerceML → Tilda → `site-links` → JSON цен → сборка YML. В основном cron нет последних двух операций. После изменения цены возможен разрыв между сайтом и рекламным фидом; текущий факт неверной цены этим не доказан. Последнее изменение JSON в Git — 13 ноября, YML — 19 ноября (правка текста). Дата коммита не доказывает дату последнего чтения неизменившихся цен.

2. **В ShoppingAds товары отбираются по диапазону цены.** Подтверждённые диапазоны: 33 500–38 500; 60 000–75 000; 39 000–45 000 ₽. Товары 33 500/38 500 находятся на границах, плата 44 910 — всего на 90 ₽ ниже верхней. Будущее изменение цены может исключить товар из группы. Перевод на categoryId1/2/3 технически возможен, но сейчас не применялся и требует отдельного согласованного изменения.

3. **Регистр артикула значим.** `DQ500` и `Dq500` — разные товары; `EA888gen3` и `Ea888gen3` — тоже. Master и каталоги связываются по точному SKU. Снимок сайта приводит ключи к нижнему регистру и не может однозначно представить такие пары. В едином документе нельзя автоматически объединять эти строки. У 12 рекламных DQ200 SKU таких коллизий нет.

4. **Master одновременно редактируется и пересобирается.** `build-master-control` предпочитает непустые цены рабочих каталогов; старые значения мастера используются лишь при пустом источнике. Дополнительные поля, включая закупку, сохраняются. Поэтому несинхронизированная правка цены в мастере может быть замещена при пересборке. Это риск последовательности операций, а не утверждение, что сейчас цена потеряна.

5. **Старый полный update-cards больше не является активной командой.** Текущий `cmd_update_cards` вызывает только `content_sync.run`. Большая `_cmd_update_cards_impl`, включая пересчёт цен и массовые отправки, осталась в файле без вызовов. В конце README есть пояснение от16.11; часть прежних help/инструкций всё ещё описывает старый процесс. Для аудита нельзя считать наличие старой функции доказательством автоматического пересчёта цен.

6. **Одинаковая цена маркетплейсов — не подтверждённый текущий инвариант.** `align-wb-prices` только меняет колонку мастера; отправка на WB отдельна. Код ручной отправки цен Маркету существует, но общий cron его не вызывает. Заявленная в комментариях внешняя синхронизация Ozon→Маркет требует проверки в кабинете; её нельзя подтвердить чтением репозитория.

7. **Результаты запусков сохраняются с ограничениями.** `save_data.sh` при параллельной правке предпочитает изменённый человеком файл целиком, а не отдельные клетки; после пяти неудачных push выводит warning и завершает кодом0. Это защищает ручной файл, но сохранение новых вычислений нужно проверять отдельно. Отчёты контента изолированы в `reports/content-sync/`; Direct-аудиты этой сессии — в новых конкретных `data/direct_*.json/tsv`, не в `data/direct/**`.

## Основа следующего ценового аудита

Сохранять по одному точному SKU: название и тип товара; закупку с источником; план Ozon/WB; план сайта/Авито; наблюдаемую цену площадки с датой получения; обычную цену и отдельное условие обмена; цену рекламного фида; действующий диапазон ShoppingAd; признак расхождения и статус проверки. Отсутствующие закупки и неоднозначные соответствия остаются явно пустыми/непроверенными. Отчёт не должен сам становиться источником публикации.

Для ценового аудита необходимо сопоставить эти существующие источники, актуальные цены площадок и реальные затраты по заказам. Изменения формул, цен, источников истины, расписаний и рекламных фильтров в рамках этого чтения не выполнялись.

## Проверенные ограничения расчёта прибыли

- `price_audit.py::build_price_audit()` может дописывать закупочную стоимость из `Ozon price.net_price` и сохранять `master_control.xlsx`. Это не полностью читающая команда; в этой проверке она не запускалась.
- В `site_pricing.py` предусмотрен резерв рекламы 10%, но показатели прибыли в сохранённых `price_audit` и `price_preview` этот резерв не вычитают. Упаковка, оплачиваемая продавцом доставка и гарантийные/возвратные затраты тоже должны быть учтены отдельно.
- `price_floor()` округляет минимум через `nice_price()`, которое может округлить вниз. При закупке 13 800 ₽ и текущих параметрах модели требуемая цена 34 936,71 ₽ превращается в 34 500 ₽; результат по той же модели 13 455 ₽ вместо требуемых 13 800 ₽. Это проверка формулы, не факт убытка по текущему товару.
- Тарифный расчёт Ozon и расчёт по финансовому факту отличаются между аудитом и ценовым планировщиком. Исторические начисления нельзя без пояснения сопоставлять с налогом от нынешней цены каталога.
- Для 12 рекламируемых SKU действуют шесть ручных фиксаций и шесть исключений по порогу дорогих товаров. Ошибки общей формулы не доказывают, что она сейчас пересчитывает эти 12 цен.
- Цена 29 500 ₽ подтверждена владельцем только при возврате старого мехатроника. Обычная цена и условное предложение обмена должны оставаться отдельными значениями.

## Правила дальнейшей работы

1. Перед изменением цены проследить точный SKU, управляющий файл, отдельные цены канала, условия обмена и конечный источник рекламного фида.
2. Сверять точный регистр SKU; неоднозначные соответствия не объединять автоматически.
3. Сначала готовить план «было → стало» и предполагаемые получатели изменения. Цены, бюджеты и работающие рекламные настройки применять только в пределах согласованного изменения.
4. Для отчётов не запускать команды с побочными записями в master. Отсутствующие закупки сохранять как отсутствующие данные.
5. После публикации проверять возвращённые значения и состояния получателя, а не только успешное завершение общего workflow.
6. Для новых исследовательских результатов использовать отдельные пути. Папка `data/direct/` имеет исторические автоматические триггеры; запись туда может запустить публикацию или отправку сообщения.
7. Расписания существуют только в явно созданных Actions/автоматизациях; наличие этой карты не означает непрерывного автономного управления.

## Источники и проверяемость

Источники кода и их прочитанные blob SHA перечислены в [source_inventory_20261120.json](source_inventory_20261120.json). Снимок охватывает 96 существовавших workflow; новый аудит ключей проверен отдельно. Для одноразовых исторических скриптов проверены триггеры и точки входа, а не повторно вся бизнес-логика.

- [README.md](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/README.md) — blob `23774625757b13f3730ad066ccc2379b5cebdae7`.
- [config.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/config.py) — blob `c6d9e1384d452c65406c8be51b373dc4248e56c3`.
- [main.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/main.py) — blob `86d8dccfe34c6a4de49950a420b7d5e4639b5d4a`.
- [build_master_control.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/build_master_control.py) — blob `2c8d8d5916236b24c2137f156b24c72f018c06b6`.
- [sync_master_control.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/sync_master_control.py) — blob `4f749b2d117029b74adfc0ef2bc1945f406f620e`.
- [site_pricing.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/site_pricing.py) — blob `26ac00f764567dac420f82a53159bda02be4b753`.
- [price_audit.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/price_audit.py) — blob `bcaa5e83fc713b8d3cd5262bddf05ee3e8ea0bc5`.
- [tilda_commerceml.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/tilda_commerceml.py) — blob `06377fdf4c98579028021e7f6a7ae1bcdce70b08`.
- [tilda_feed.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/tilda_feed.py) — blob `ce46e63aced49f618de78b4d6149363118b75456`.
- [avito_feed.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/avito_feed.py) — blob `4da3a37296c675505ebd1adcc997a51bb06d15a6`.
- [ozon_client.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/ozon_client.py) — blob `6fdc4c6491c8238a7da4b18c98debb76415d7eb2`.
- [wb_client.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/wb_client.py) — blob `2436df096efb826906ea5f590f646525cf62ce65`.
- [yandex_market_client.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/yandex_market_client.py) — blob `31930a598133a08d42ddb11ce360b3edfffe6ca9`.
- [stock_sync.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/stock_sync.py) — blob `385bcaf69cf4be54c74517dc8082e6b4be18060f`.
- [content_sync.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/content_sync.py) — blob `8ef96b81a4cae12f232702a1937ec0bd23fef13d`.
- [content_transport.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/content_transport.py) — blob `f4f95da54ed5ac5dea9fec0a7bc3e8e3349770ef`.
- [content_manifest.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/content_manifest.py) — blob `cb93ee2f9076d8de32043fe869ec63812a72d206`.
- [exchange_items.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/exchange_items.py) — blob `3888a0c4aeb9bf3537f592a443d555e379537401`.
- [new_product.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/new_product.py) — blob `cdba7574558066639d016c240f33d90885dfa90a`.
- [kits.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/kits.py) — blob `111cc950a300ab20910f2034194d0c3ac4516cd9`.
- [scripts/build_dq200_feed.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/scripts/build_dq200_feed.py) — blob `8efc094fdc6a55c1e721d60f5274135fb5fabed7`.
- [data/site_prices.json](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/data/site_prices.json) — blob `6a4cdacf682f057310cd7b2a593072544dfe5558`.
- [data/ozon_overrides.json](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/data/ozon_overrides.json) — blob `651e7c5b8ddb60426f774f264e9198b0f9e2cecd`.
- [.github/workflows/main.yml](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/workflows/main.yml) — blob `dcf4f6b8824c41de200196b55139adb8aeea0ca4`.
- [.github/workflows/run-command.yml](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/workflows/run-command.yml) — blob `57835f05d953c175cc0d73b405503ae2570a9a24`.
- [.github/workflows/sync-prices.yml](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/workflows/sync-prices.yml) — blob `3cbd5e37245d256abca4986a0e151f44b0d48f76`.
- [.github/workflows/auto-update-cards.yml](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/workflows/auto-update-cards.yml) — blob `9c662e341281b3487ee041795ab23e51ce2ffb71`.
- [.github/workflows/dq200-ad-feed.yml](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/workflows/dq200-ad-feed.yml) — blob `96167ec34960d77b4109f9e35c303763c8cb5b7d`.
- [catalog_editor.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/catalog_editor.py) — blob `36ba1001a611f9d75b1fb63e68cd8208be399898`.
- [wb_catalog_editor.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/wb_catalog_editor.py) — blob `1edb7e910684b96c156da2afda0232e2f3a2cf3e`.
- [.github/scripts/save_data.sh](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/.github/scripts/save_data.sh) — blob `32064e6dd86fbed1cd7e2eef67e28a080d2d830a`.
- [site_links.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/site_links.py) — blob `e11663a9863ffb94e4a9399121c1e093248b187c`.
- [audit.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/audit.py) — blob `871824d1271513339ca6aeb1df066bc3957ab5a6`.
- [social_publish.py](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/social_publish.py) — blob `c33d64df092b2a1153921fe6f3eb2953b2abdca4`.
- [data/master_control.xlsx](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/data/master_control.xlsx) — blob `8d3f24e49d09edbf7d74c1c5c4e4156c9808b148`.
- [data/avito_site_price_overrides.xlsx](https://github.com/antongrigorenko844-prog/marketplace-agent/blob/main/data/avito_site_price_overrides.xlsx) — blob `960139c03a45ee8971a8ca63f703fce2a9cdf2f4`.

Фактические журналы выполнения:

- [Синхронизация Ozon: 0 расхождений, 0 отправок, 0 ошибок](https://github.com/antongrigorenko844-prog/marketplace-agent/actions/runs/44540016178).
- [Основной запуск: сборка 124 Avito-позиций и завершение push-tilda](https://github.com/antongrigorenko844-prog/marketplace-agent/actions/runs/44512261963).
- [Свежая инвентаризация Директа](https://github.com/antongrigorenko844-prog/marketplace-agent/actions/runs/44577367861).

Показатели цен в этом обзоре относятся к прочитанным файлам. Текущие покупательские цены, действующие комиссии и финансовые результаты на всех площадках отдельно в реальном времени не проверялись. Цены, остатки, кампании, формулы расчёта и расписания в рамках архитектурного аудита не изменялись.
