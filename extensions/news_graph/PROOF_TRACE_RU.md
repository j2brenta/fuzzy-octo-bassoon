# Границы доказательств и связь с кодом

Команда `python3 extensions/news_graph/proofs/check.py --lean /absolute/path/to/lean`
проверяет 38 теорем в Lean 4.8.0 без Mathlib: 34 в `NewsGraph.lean` и 4 в
`SelectionBudget.lean`. Toolchain и приватные материалы review не включены в Git.
Вход для команды: корень checkout либо абсолютный путь к скрипту.

## Карта соответствия

Пути Python ниже относительно `src/newsmap_sidecar/`, JavaScript относительно
`src/newsmap_sidecar/assets/`, тесты относительно `tests/`.

| Свойство Lean | Код и регрессия | Граница утверждения |
|---|---|---|
| `exact_span_has_bounds_and_quote` | `adapter.py`; `test_formal_trace.py::test_shared_unicode_span_uses_codepoints_not_utf16` | Unicode scalar/code points, не UTF-16 offsets. Декодирование и resolver проверяются отдельно. |
| `import_does_not_transfer_approval` | `intake.py`, `review.py::_show`; общий revision trace | Уже удержанные immutable bindings, сохранение head. Восстановление недостающего evidence и изменение resolver binding не входят в этот переход. |
| `changed_binding_inactive`, `changed_right_binding_inactive`, `unresolved_anchors_inactive` | `review.py::_show`; `test_review.py`, `test_formal_trace.py` | Gates admission/quality, непустых точных anchors и совпадения payload/anchor digest вычисляет Python. Булевы поля Lean представляют результат проверки. |
| `revoked_premise_inactive`, `same_binding_reselection_restores` | `review.py`, `storage.py`; общий trace и тест правой ревизии | Возвращается применимость при прочих прежних условиях, не прежний token. |
| `reselection_does_not_restore_token`, `stale_review_noop` | `_show` включает `head_token`; `Database.select`; общий trace | Nat абстрагирует свежие события. Коллизии хеша и конкурентность SQLite не доказаны Lean. |
| `blank_rationale_noop`, `failed_acceptance_noop` | `review.py::review`; тест пустого rationale и revision trace | `submitReview` проверяет текст, `reviewCommand` описывает уже валидированную команду. No-op соответствует исключению без записи. Python/Lean whitespace-классификация не доказана эквивалентной для всех символов. |
| `review_preserves_history_consistency`, `review_history_not_erased` | журнал `events`, `review.py::_show`; `test_review.py` | Согласованная начальная история; доказательство core-перехода. Не модель дисковых сбоев. |
| `human_and_model_labels_distinct`, `rerun_preserves_human_rejection` | `automation.py::assess`, `review.py::_show`; общий trace | Повторное использование существующего proposal с неизменяемым происхождением. Новые proposals имеют отдельное состояние. |
| `page_edge_bound`, `page_canonical_and_closed` | `app.js::graphSlice`; общий browser trace с 71 входным ребром | Входные узлы уже упорядочены и сфокусированы. Каждая страница ограничена cap; каждое ребро прошло фильтр и оба конца входят в отображаемые узлы. |
| `resolved_exact`, `resolved_unique_context`, `changed_mode_clears_relation` | `app.js::selectEdge/renderComparison/refresh`; общий browser trace и `test_view_contracts.py` | ID уникальны в DTO; refresh очищает исключённый контекст. Положение узлов и удобство чтения не доказаны. |
| `hidden_append_preserves_success_or_failure`, `public_current_not_disclosed`, `unclosed_history_not_exported` | `projection.py::build`; `test_public_history.py`, `test_export.py` | Постоянный allowlist, зависимости только раскрытой истории. `evidenceAuthorized` абстрагирует полевые и цитатные grants. Не доказан весь сериализатор. |
| `exact_selection` | `app.js::selectEdge/renderComparison`; `test_view_contracts.py` | Более ранняя малая модель точного ID и контекста. |
| `bill_not_hidden`, `overrun_freezes`, `bounded_bill_safe` | `metering.py::Ledger.reconcile`; `test_shared_lean_breach_trace_matches_concrete_accounting` | Достоверные receipt/тариф, полный охват отправок. При overrun фактический расход сохраняется, отправка замораживается; cap уже мог быть нарушен. |

## Три разных вида проверки

1. **Доказательства Lean.** Все теоремы проходят ядро. Каждый вывод зависимостей
   аудируется через `#print axioms`; нет `sorry`, пользовательских аксиом и
   `native_decide`. Разрешены стандартные `propext`, `Classical.choice`, `Quot.sound`.
2. **Общий исполняемый пример.** `proofs/news-trace.json` читают Lean и
   `test_formal_trace.py`. Проверяются review, новые ревизии, stale-команды, повторная
   model-оценка, отзыв предпосылки, emoji/combining accent, точное обратное ребро и
   вторая страница графа. Это один конечный сценарий, не доказательство всех входов.
3. **Регрессии приложения.** Тесты используют настоящие SQLite и Chrome с отключённым
   GPU. Они дополнительно проверяют repair binding, grants, фильтры и ошибки.
   Независимые Grok/Argon reviews ищут пропуски в этих трёх слоях, но сами их не доказывают.

## Что сознательно не доказано

Целые ID/digests в Lean обозначают неизменяемые scoped-идентификаторы. Их уникальность,
криптографическая стойкость, разбор JSON, точное соответствие байтам, корректность
Python/SQLite/браузера и сохранность диска остаются внешними условиями. Ремонт evidence
с тем же ClaimRef требует отдельной проверки binding; его нельзя обосновать теоремой
о простом добавлении immutable revision. Такой ремонт покрыт существующим
`test_missing_evidence_binding_requires_new_proposal_after_repair`.

Модель отображения допускает исторические, отклонённые и предлагаемые связи в тех
фильтрах, где их разрешает caller. Видимое ребро не обязано быть активным review.
`resolved_exact` доказывает совпадение ID и направления; только отдельная теорема
`resolved_unique_context` использует предположение уникальности ID.

Публичный snapshot показывает разрешённую историю и не сообщает текущую приватную
применимость. Private-отзыв не меняет такой snapshot при прежнем allowlist. Для
раскрытия отзыва надо разрешить его явно. Проверка closure может отказать в экспорте,
если разрешённому решению недостаёт разрешённых зависимостей или evidence.

Модель не доказывает bound токенов live-провайдера. Текущий metering применяется к
`OFFLINE_SYNTHETIC`, paid cap 0. Loopback проверяет физический локальный HTTP-запрос,
а не облачный биллинг. GPU, платный canary и нативный Mac остаются отдельными проверками.

Практическое введение для команды: [GRAPH_LEAN_RU.md](GRAPH_LEAN_RU.md).
