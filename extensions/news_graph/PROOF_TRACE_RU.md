# Границы доказательств и связь с кодом

`proofs/SelectionBudget.lean` компилируется Lean 4.8.0 без Mathlib.
Внешние toolchain и приватные исследовательские материалы в Git не включены.

| Свойство | Предположения | Исполнение и регрессия |
|---|---|---|
| exact_selection | Уникальные ID в неизменяемом snapshot; правильный разбор JSON | app.js selectEdge/renderComparison; test_view_contracts.py выбирает обратное ребро при другом ребре той же пары, затем исключает фильтром |
| bill_not_hidden | Уже проверен receipt, целые micro-USD | Ledger.reconcile; test_shared_lean_breach_trace_matches_concrete_accounting читает тот же proofs/trace.json |
| overrun_freezes | Выставленный расход больше резерва | reconcile сохраняет настоящий оценённый charge и frozen; следующая reserve отказывается |
| bounded_bill_safe | Достоверный тариф, полный охват отправок и расход в пределах оценки | reserve/dispatch, outbound loopback spy и сохранённый receipt; при overrun предположение нарушено, не заявляется сохранение cap |

Четыре теоремы проверены Lean. Используются стандартные propext, Classical.choice,
Quot.sound; пользовательских аксиом и sorry нет. Это доказательства абстрактных
переходов, а не доказательство Python, SQLite, браузера или провайдера.

Приватная исходная модель review проверяет version binding, отзыв предпосылки,
сохранение истории и повторный выбор той же immutable revision. Production-тесты
`test_review.py` проверяют соответствующие переходы, а public history отдельно
проверяется в `test_public_history.py`. Исходная модель не описывает раскрытие
публичной истории и реальную физическую отправку. Её over-bound settlement был
no-op; этот переход нельзя выдавать за соответствие production. Новая небольшая
модель выше явно сохраняет bill и frozen.

До исправления browser-регрессии находили неверный контекст, 92 ребра вместо cap60
и лексическую сортировку offset timestamps. После исправления проверяется реальный
Chrome с отключённым GPU. Проверка сборки доказательств не заменяет эти тесты.

Модель не доказывает существование token bound у ещё не выбранного live-провайдера.
Текущий bound относится только к OFFLINE_SYNTHETIC; paid cap0. Прогон на localhost
проверяет байты одного физического HTTP-запроса, а не облачный биллинг.
