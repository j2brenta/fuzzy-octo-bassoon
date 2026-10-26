# News Map — локальная карта новостей и доказательств

News Map — отдельный читатель опубликованных артефактов Refresher. Он сохраняет
импортированные снимки и точные цитаты в SQLite. Producer JSON остаётся источником истины; расширение не переписывает исходный
vault. История начинается с первого импорта: программа не восстанавливает
пропущенные версии и не делает вывод об удалении или отзыве новости по её
отсутствию в очередном сканировании.

Реализованы сохранение доказательств, журнал версий, редакционные назначения,
ручной review, опциональные MODEL_ASSESSED оценки и согласованный офлайн-интерфейс: темы и истории, хронология,
ограниченный 2D-граф, точные цитаты и сравнение условий. Области графа соответствуют
редакционным темам; положение узла не означает причинность или авторитет источника.

Короткий [мануал для команды: граф, review и Lean](GRAPH_LEAN_RU.md) объясняет,
как читать связи и проверять изменения.

## Быстрая синтетическая демонстрация

Из корня checkout выполните команды, задав новый каталог на постоянном локальном
диске вне Git. Вторая команда обслуживает **только экспорт**, а не vault или базу:

```bash
demo_dir="$HOME/newsmap-demo-$(date +%Y%m%d-%H%M%S)"
python3 extensions/news_graph/run.py demo "$demo_dir"
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$demo_dir/viewer"
```

Откройте `http://127.0.0.1:8765/`. Demo содержит девять синтетических материалов,
замену одного package, три ручные истории, перепечатки, другую конфигурацию теста,
pending-оригинал, совпадающие имена и отдельно импортированный held-out материал.
Все editorial/review действия подписаны `fixture_operator`. Генерация этой
демонстрации не вызывает модели и не является live AI extraction.

1. Выберите тему «Модели и архитектуры», затем историю Aurora. В режиме источников
   проследите две перепечатки до одного URL-ресурса. Это общий наблюдаемый reference,
   а не два независимых подтверждения.
2. Нажмите стрелку: справа появятся смысл связи и точная сохранённая цитата.
   Колесо мыши/кнопки меняют масштаб; фон можно перетаскивать. Выбор подсвечивает
   ближайшие связи; кнопка окружения ограничивает видимый граф соседями.
3. Переключитесь на утверждения и откройте `CORRECTS`. Внизу появятся обе версии
   цитат, все непоследовательные фрагменты и контекст, явно добавленный оператором.
4. В теме оценки сравните benchmark и конфигурацию двух GPU. В самих цитатах
   различаются датасет, метрика и условия; программа не создаёт противоречие
   автоматически и не заполняет неизвестные параметры догадками.
5. Фильтр `pending` оставляет оригинал Orion читаемым, но не делает его допустимым
   endpoint активной смысловой связи. Включите невыбранные ревизии, чтобы увидеть
   старый package Aurora и историческое review без переноса на новую версию.
6. Held-out материал появляется через обычный `import` и `story assign`.
   Код потребителя не содержит отдельной ветки по его ID.

В графе не более 20 основных узлов и 60 рёбер. Дополнительные узлы и связи доступны страницами текстового списка; выбор сохраняет точный ID и направление решения. Тематические рамки не являются
новыми источниками. Скрытое количество видно явно; полный список материалов и
текстовый список узлов/связей доступны независимо от canvas. Фильтры синхронизируют
граф, цитаты и сравнение: исключённый контекст очищается. Числа в верхней строке
считают сохранённые ревизии; timeline по умолчанию показывает выбранные.

Граф помогает увидеть повторное цитирование одного URL и путь происхождения.
Таблица и сами цитаты удобнее для различий условий. Это функциональный walkthrough,
а не измеренная победа интерфейса над списком: человеческий usability-пилот пока
не проводился. Файл `demo-receipt.json` содержит фактическое время скрипта и число
синтетических решений, не выдуманное время работы человека.

## Запуск без установки основного проекта

Нужны Python 3.11+ и постоянный локальный диск. Linux проверен; для macOS добавлена проверка локального APFS через statfs64, но нативный прогон на Mac ещё не выполнен. Рабочее хранилище,
резервные копии и экспорт должны находиться вне Git-репозиториев и исходного vault.
Приложение использует только стандартную библиотеку Python; подключение к моделям,
загрузка `.env` и фоновые сетевые запросы отсутствуют.

Из корня checkout, подставив собственные абсолютные пути:

```bash
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite init
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite register \
  --namespace news --vault /path/to/idle-vault --actor operator
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite import \
  --namespace news --vault /path/to/idle-vault --scope processed
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite list
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite inspect \
  --namespace news --item ITEM_ID
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite doctor
```

Импортируйте неподвижный snapshot или неиспользуемый producer-vault. Каждая запись
проверяется отдельно; это не атомарный снимок всей файловой системы. Изменение
прочитанных файлов во время batch помечает batch как `changed`, а оборванный
процесс оставляет видимую запись `running`. Повторный импорт безопасен: новый
batch добавляет наблюдение, но не дублирует package-ревизию.

Один batch обрабатывает не более 200 файлов; оставшийся хвост отмечается `deferred`,
его можно импортировать отдельными `--scope processed/ITEM_ID.json`; один файл — не более 10 MiB.
Лимиты нового хранилища: сохранённые входы и разрешённые evidence payloads 32 MiB, метаданные 8 MiB, файл SQLite 64 MiB. Для rollback journal и временных копий нужен отдельный запас места. Экспорт допускает до 200 ревизий/claims, 2000 событий, 8 MiB входных данных и 8 MiB готового JSON; превышение возвращает ошибку до создания viewer. Неподдерживаемые имена файлов,
symlink, hardlink, специальные файлы и неоднозначные/bind mount boundaries отвергаются. При превышении лимита
доказательства не обрезаются и прежние данные не вытесняются. Карантин хранит
допустимые по размеру байты и причину отказа. Если байты не удалось сохранить
полностью, явно записывается `full_bytes_retained=false`.

## Что именно проверяется

Поддержаны `handoff-v1` без `quality_policy_version` и
`handoff-v2` с `correctness-v1`. В bundle fingerprint входят все поля JSON,
кроме `bundle_id` и внешнего `created_at`, включая неизвестные поля. Смена
форматирования JSON не создаёт новую package-ревизию, но её точные байты сохраняются
как отдельный артефакт. Неизвестный контракт отделён от повреждённого fingerprint.

Хеш текста вычисляется по точному UTF-8 представлению `raw.text`, без нормализации
пробелов или Unicode. Цитаты используют полуоткрытые диапазоны Unicode code points
и сохраняют порядок всех непоследовательных фрагментов. Это не индексы UTF-16
JavaScript. Совпадение текста при несовпадении исторического raw-объекта помечается
как `text_match_metadata_unconfirmed`: изменённые raw-метаданные не становятся
новыми доказательствами авторства или происхождения.

Раздельно показываются целостность доказательств, upstream quality, выбранная
ревизия и история наблюдений. `accepted` означает результат проверок extraction
в producer, а не независимое подтверждение правдивости новости. Сам хеш проверяет
внутреннюю согласованность; он не является подписью издателя.

Первая корректная package-ревизия выбирается явно записанным bootstrap-событием.
Следующие версии доступны для просмотра, но не заменяют выбранную автоматически:

```bash
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite select \
  --namespace news --item ITEM_ID --bundle BUNDLE_ID \
  --expect-current HEAD_TOKEN_FROM_LIST --actor operator
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite history
```

Токен содержит не только bundle, но и событие выбора: переключение A → B → A
не делает старую команду снова актуальной. Устаревшая команда возвращает `stale`,
блокировка другого writer — `busy_retry`. Решения дописываются; прежние события
не обновляются и не удаляются.

## Зачем здесь граф

Граф состоит из узлов и направленных связей с конкретным смыслом. Документ,
его утверждение, цитируемый URL и упоминание человека — разные типы объектов.
Ссылка на публикацию означает «ссылается на», а не «подтверждает». Одинаковые
имена не доказывают, что речь об одном человеке; несколько перепечаток не
доказывают независимость источников.

Для такого небольшого графа отдельный графовый сервер не обязателен. SQLite
сохраняет версии, ссылки и решения; визуальное расположение узлов — способ
навигации, а не источник доказательств. Ручное объединение материалов в историю
должно иметь автора и обоснование. Интерпретации «поддерживает», «противоречит»
и «исправляет» привязаны к точным версиям и цитатам. MODEL_ASSESSED можно читать без обязательного ручного одобрения; HUMAN_REVIEWED означает отдельное действие оператора. Ни один статус не является независимой проверкой истины.

## Восстановление

Резервная копия создаётся поддерживаемым SQLite backup API. Не копируйте открытый
файл базы обычной файловой командой. Выберите новый файл в отдельном каталоге:

```bash
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite backup \
  /path/to/separate-backup/news.sqlite
python3 extensions/news_graph/run.py --db /path/to/local-state/news.sqlite restore-check \
  /path/to/separate-backup/news.sqlite
```

Копия содержит retained envelopes, исходный текст и прочитанные raw-байты,
карантин, наблюдения, выбор ревизий и события. Она работает без source-vault.
Повторный импорт не заменяет backup: он не восстанавливает потерянные решения.
Вложения, изображения и веб-страницы не загружаются; сохранённая ссылка на них
не означает доступности их содержимого офлайн.

## Независимые проверки

```bash
python3 -m pytest -q extensions/news_graph/tests
```

Набор использует только синтетические данные. Он проверяет ошибки хешей и JSON,
политики quality, Unicode и множественные цитаты, изменение raw-метаданных,
идемпотентность, ревизии, path/symlink/FIFO-атаки, лимиты, аварии процесса,
конкурентных writer и восстановление. CLI также проверяется с `python -S`, без
site-packages и зависимостей producer. Существующий host-suite запускается
отдельно; успех этого набора не означает успех host-suite или live AI pipeline.


## Редакционная организация и review

Команды принимают точные JSON-ссылки: `namespace`, `item`, `bundle` и, для
утверждения, `claim`. Значения можно взять из `inspect` или частного `view-data`.
В примерах `ITEM`, `BUNDLE`, `CLAIM`, `TOKEN` и числовые ID нужно заменить
полученными значениями. Любое решение требует `--actor` и `--rationale`.

```bash
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" story create \
  --title 'Новая история' --topic models --actor operator --rationale 'Ручная группировка' \
  --evidence '[{"namespace":"news","item":"ITEM","bundle":"BUNDLE"}]'
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" story assign \
  --target '{"namespace":"news","item":"ITEM","bundle":"BUNDLE"}' --value 123 \
  --evidence '[{"namespace":"news","item":"ITEM","bundle":"BUNDLE"}]' \
  --actor operator --rationale 'Принадлежность этой версии к истории'
```

`topic assign` использует такой же `--target`, а `--value` — один из ключей темы
(`models`, `evaluation`, `agents`, `multimodal`, `infrastructure`, `research`,
`safety`, `policy`, `products`, `open-source`). Определения тем находятся в
`src/newsmap_sidecar/assets/topics.json`. `subject assign --value` принимает
`{"subject":"Aurora","version":"1"}`. Возможны несколько членств и незаполненная
папка входящих. Отзыв назначения: `assignment revoke ID --actor ... --rationale ...`.

```bash
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" relation propose \
  --left '{"namespace":"news","item":"LEFT_ITEM","bundle":"LEFT_BUNDLE","claim":"LEFT_CLAIM"}' \
  --right '{"namespace":"news","item":"RIGHT_ITEM","bundle":"RIGHT_BUNDLE","claim":"RIGHT_CLAIM"}' \
  --type CONTRADICTS --actor operator --rationale 'Ручная интерпретация для проверки' \
  --context '{"subject":"Aurora","version":"1","metric":"score","dataset":"unknown","unit":"points","conditions":"unknown","time_attribution":"unknown","unknowns":"independence"}' \
  --evidence '[{"namespace":"news","item":"LEFT_ITEM","bundle":"LEFT_BUNDLE","claim":"LEFT_CLAIM"},{"namespace":"news","item":"RIGHT_ITEM","bundle":"RIGHT_BUNDLE","claim":"RIGHT_CLAIM"}]' \
  --premises '[]'
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" review show 123
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" review accept 123 \
  --expect-token TOKEN --actor operator --rationale 'Проверены обе версии и все фрагменты'
```

Сохранён словарь исходного графа: `SUPPORTS`, `CONTRADICTS`, `SUPERSEDES`, `DUPLICATES`, `ELABORATES`; расширение также поддерживает `CORRECTS`. Доступны действия `review reject`, `review defer`
и `review revoke`. Перед действием всегда получите свежий токен через `review show`.
Предложение закрепляет payload и весь список anchors. Смена выбранного package
делает старое review историческим; оно не переносится на новые claims.

`identity list` показывает локальные роли без объединения по имени.
`identity link --left ID --right ID --evidence JSON --actor ... --rationale ...`
создаёт обратимую интерпретацию, не физическое слияние записей. Для её отзыва есть
`identity revoke ID`. Использованные `identity_link` и `subject_assign` включаются
в `--premises` предложения. Их отзыв немедленно даёт `needs_recheck`, сохраняя
старое одобрение. Неиспользованное назначение не инвалидирует другие review.

## Офлайн-экспорт и граница раскрытия

```bash
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" export-local /path/to/new-viewer
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" export-public /path/to/new-sanitized-viewer \
  --allowlist /path/to/operator-allowlist.json
```

Обе команды создают новый локальный каталог. Они ничего не публикуют и не
перезаписывают существующие каталоги. Экспорт отделён от authority, backup и vault.
Cytoscape **3.33.1** поставляется локально с MIT-лицензией и SHA256 в
`src/newsmap_sidecar/assets/vendor-manifest.json`. Во время просмотра нет CDN,
телеметрии, web previews, remote fonts и модельных вызовов. Внешние http/https
ссылки открываются только по явному действию; недопустимые схемы не кликабельны.

Пример структуры allowlist (индексы `spans` и `links` начинаются с нуля):

```json
{
  "actor": "operator",
  "items": [{
    "namespace": "news", "item": "ITEM", "bundle": "BUNDLE",
    "fields": ["source", "time", "quality"],
    "claims": [{"claim": "CLAIM", "statement": true, "spans": [0, 1], "surrounding": false}],
    "links": []
  }],
  "decisions": []
}
```

`fields` дополнительно допускает `url` и `roles`. `source` раскрывает display name,
а не локальный file/source ID; `roles` — известные поля name/handle/url/kind,
без email и неизвестных полей. `surrounding: true` отдельно разрешает соседний
контекст. Только перечисленные цитаты и claims попадают в минимальные DTO.
Полные envelopes, неизвестные поля, raw JSON и SQLite никогда не включаются
в viewer. Публичные IDs создаются заново внутри разрешённой проекции.

Ручной заголовок, rationale, subject и review требуют собственных ID в `decisions`.
Все evidence references и premises такого объекта тоже должны замыкаться на
разрешённый набор. Для публичного ручного текста evidence-ссылка должна называть
конкретное утверждение со всеми разрешёнными spans и statement: широкая ссылка
только на item не доказывает, какие закрытые фрагменты повлияли на заголовок,
поэтому явно запрошенное решение с незамкнутыми зависимостями вызывает ошибку экспорта. Target назначения может оставаться item. Наличие публичного узла не разрешает приватное обоснование.
Счётчики, темы и topology вычисляются после фильтрации. Публичное review означает
одобрение конкретной пары версий; приватные текущие selection heads не раскрываются,
и публичный экспорт не выдаёт это review за глобально активное.

Режим `view-data` предназначен для частной инспекции authority, а не для публикации.
Никакой allowlist не заменяет решение оператора о правах на распространение данных.

## Повторяемые интеграционные и браузерные проверки

Обычные тесты расширения не требуют host-зависимостей. Для development-only
producer/browser проверок установите host dependencies, pytest и Playwright в
отдельное виртуальное окружение **вне checkout**. Не загружайте `.env`.

```bash
PYTHON_DOTENV_DISABLED=1 /path/to/test-env/bin/python \
  extensions/news_graph/tests/producer_smoke.py "$PWD" /path/to/new-producer-smoke
/path/to/test-env/bin/python extensions/news_graph/tests/browser_check.py \
  "$demo_dir/viewer" /path/to/browser-reports
/path/to/test-env/bin/python extensions/news_graph/tests/browser_safety.py \
  /path/to/new-browser-safety /path/to/new-producer-smoke/viewer
PYTHON_DOTENV_DISABLED=1 /path/to/test-env/bin/python -m pytest -q
```

Browser harness использует `/usr/bin/google-chrome`; `browser_check.py` также
принимает путь к Chrome третьим аргументом. Он проверяет реальные pointer clicks,
pan/zoom, topic/story navigation, edge drill-down, review/quality/role filters,
сброс исключённых evidence, сравнение всех spans, mobile overflow и отсутствие
внешних запросов. Отдельный hostile fixture проверяет `</script>`, опасные URL,
инертный HTML и доступ к producer-цитатам после отключения сети браузера.

Producer smoke запускает существующий `channels.importer`, parser, extraction,
judge/quality, entity extraction/review и packaging в scratch vault. Только ответы
модели заменены явно синтетическими `ModelReply` через существующий client protocol.
Это проверка настоящих producer-функций и интеграции, **не live model E2E** и не
оценка качества модели. На исходном окружении host-suite может не собраться из-за
отсутствующих зависимостей — это отдельный результат, а не успех extension-suite.

Для восстановления производного индекса выбора существует `rebuild`: он повторяет
selection-события в исходном порядке, не читая vault и не меняя review. `doctor`
проверяет SQLite integrity, foreign keys и digest сохранённых blobs.

## Ограничения и результат экспериментов

- Источник должен быть неподвижным или snapshot; непрерывного ingestion hook нет.
- MANUAL сохраняет ручную организацию. NEBIUS пока исполняется только с явно синтетическим transport: автоматические назначения, версии и оценки сохраняются с provenance. Live-качество модели, fact-checking и рейтинг экспертов не подтверждены.
- Поддержан текстовый producer-контракт; вложения остаются недоступными ссылками.
- 2D — обязательный рабочий путь. Отдельный локальный 3D spike на закреплённом
  `3d-force-graph` 1.79.0 показал вращение камеры и доступ к evidence, но у 12
  подписей были 6 пересекающихся пар. Он не включён в поставку из-за читаемости.
- Neo4j не установлен: для проверенного ограниченного графа не выявлена функция,
  оправдывающая второе хранилище; смена базы сама по себе не устраняет наложение подписей.
- Автоматизированные browser checks не заменяют человеческий usability-пилот.

Возможные улучшения host, **не реализованные здесь**: неизменяемая публикация
версий, формально версионированный контракт артефактов и отдельная интеграционная
CI-проверка после согласования с сопровождающими. Существующие ingestion, legacy heuristics, root dependencies и root CI не изменяются. Единственная согласованная host-правка: необязательный request_transport у NebiusModel; обычный CLI использует прежний transport и остаётся вне гарантий metered runner.


## Два режима и происхождение оценок

В MANUAL оператор назначает тему/историю и предлагает интерпретацию. В NEBIUS
адаптер передаёт сохранённые цитаты и ограниченный набор пар модели, затем сохраняет
результат в том же журнале. Совпадение слов используется только для поиска кандидатов,
а не как доказательство SUPPORTS. Исходные extraction, quality и entity stages не
дублируются. Для автоматической оценки допускаются 12 материалов, 24 claims и 8 пар;
непоместившиеся материалы явно считаются отложенными. Слишком большие цитаты не обрезаются.

Сейчас доступен только `OFFLINE_SYNTHETIC` с `fixture-model`. Это тест границ
пайплайна, а не качество Nebius. Результат помечен `SIMULATED_OFFLINE`. Он использует
содержимое входов, а не заранее заданные ID. При недостаточном контексте сохраняется
`not_comparable`/`insufficient_evidence`. Просмотр и фильтры не вызывают модель.
Человеческое отклонение не затирается повторной оценкой; новая ревизия не наследует
одобрение предыдущей. Отсутствующий в репозитории graph-live не был источником кода.

```bash
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" assess-offline --mode MANUAL
# Для NEBIUS нужен отдельный JSON-манифест и существующее окружение producer.
# Полный пример: WALKTHROUGH_EN.md. Платный лимит всегда остаётся нулевым.
```

## Публичная история и хранение

Публичный экспорт показывает разрешённую историю: `review_record` и
`current_applicability=not_disclosed`. Скрытый новый review, отзыв предпосылки или
смена private head не меняют содержимое при прежнем allowlist. Для раскрытия отзыва
нужно явно добавить его ID. Это не утверждение о текущей применимости старого решения.
Даты сортируются и фильтруются по UTC; исходная строка и основание даты сохраняются.

В schema 2 одинаковый evidence payload хранится один раз вместе с версией resolver.
Каждый скан всё равно добавляет наблюдение. Поэтому небольшой рост метаданных ожидаем.
Schema 1 открывается для чтения; автоматического обновления нет. Создание новой копии:

```bash
python3 extensions/news_graph/run.py --db /path/to/old/news.sqlite migrate-copy /path/to/new-state/news.sqlite
```

Копия проверяется по семантическому snapshot, IDs, событиям, evidence и heads.
Исходник не заменяется. Повреждение, отсутствие зависимостей или прерывание не
публикуют готовую копию. Миграция реальных данных требует отдельного решения оператора.
Подробнее о механике: [SQLite backup API](https://www.sqlite.org/backup.html).

## MacBook

Для demo выберите каталог в `$HOME` на локальном APFS, вне Git и синхронизируемых
папок iCloud/Dropbox. Откройте экспорт через локальный HTTP-сервер из примера выше.
Никакие системные службы устанавливать не нужно. Карта поддерживает перетаскивание,
колесо/трекпад, кнопки масштаба и текстовый список. На небольшом экране панель
доказательств переносится вниз. Native Safari/Chrome, блокировки SQLite и восстановление
на APFS ещё требуют проверки на настоящем Mac; Linux-проверка похожего viewport это не заменяет.

Guard проверяет MNT_LOCAL и APFS, а SQLite использует fullfsync на Darwin.
[Рекомендация Apple по проверке локального тома](https://developer.apple.com/library/archive/qa/nw09/_index.html).

## Проверки и оставшиеся ограничения

Lean-модель и её связь с исполняемыми проверками описаны в [PROOF_TRACE_RU.md](PROOF_TRACE_RU.md).
Она не доказывает весь Python/JavaScript, подлинность ответов провайдера или правдивость новостей.
Офлайн runner резервирует точный payload перед физической попыткой, сохраняет usage до
разбора JSON, удерживает неопределённый расход после timeout и замораживает отправку при
превышении оценки. Тариф и token envelope синтетические. Подключение реального транспорта,
токенизатора и тарифа провайдера остаётся отдельным gate. Новая stage не обнуляет общий ledger.
Общий бюджет владельца $25, из него $5 защищены; это потолок, а не цель расходования.
