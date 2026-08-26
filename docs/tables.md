# Документация таблиц

Данные живут в двух базах. **ClickHouse** — аналитическое хранилище: события
и витрины, здесь выполняется весь анализ. **PostgreSQL** — продуктовая база:
пользователи, конфигурация экспериментов, абшница.

Всё аналитическое время — **виртуальное** (время симуляции). Реальное время
встречается только в служебных колонках и в анализе не используется.

---

## ClickHouse

### ab.events — поток продуктовых событий

Главная таблица анализа. Одна строка — одно событие пользователя.

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `event_date` | Date | Виртуальная дата события. Таблица партиционирована по месяцам этой даты — фильтр по ней ускоряет любой запрос |
| `event_ts` | DateTime | Виртуальное время события с точностью до секунды |
| `user_id` | UInt64 | Пользователь, ключ к `core.users` |
| `session_id` | String | Сессия: события одного захода в приложение имеют общий id |
| `event_name` | LowCardinality(String) | Шаг воронки, см. ниже |
| `region` | LowCardinality(String) | Регион пользователя |
| `platform` | LowCardinality(String) | `ios` / `android` |
| `tariff` | LowCardinality(String) | `uberx` / `comfort` / `uberxl` |
| `price_low` | Nullable(Float64) | Нижняя граница диапазона цены — заполнена, если пользователь видел диапазон |
| `price_high` | Nullable(Float64) | Верхняя граница диапазона |
| `price_shown` | Nullable(Float64) | Точечная цена — заполнена, если пользователь видел точечную оценку (тестовые группы) |
| `price_actual` | Nullable(Float64) | Фактическая цена поездки, только у `trip_complete` |
| `inserted_at` | DateTime | Служебное: реальное время вставки. В анализе не использовать |

Воронка `event_name`:

```
app_open → screen_view → tariff_select → order_confirm → trip_complete
                                                       ↘ order_cancelled
```

Поля цены заполнены начиная с `screen_view`; у `app_open` и `order_cancelled` цен нет.

### ab.assignments — реплика абшницы

Копия `ab.assignments` из Postgres, сюда её каждые 45 секунд доливает фоновый
воркер. Нужна для join-ов с событиями внутри ClickHouse.

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `experiment_id` | UInt32 | Эксперимент |
| `user_id` | UInt64 | Пользователь |
| `variant` | LowCardinality(String) | Группа: A / B / … |
| `assigned_at` | DateTime | Виртуальный момент первого попадания в эксперимент (exposure) |

Движок ReplacingMergeTree допускает временные дубли строк — **читай через
`FINAL`**: `FROM ab.assignments FINAL`. В метриках считай только события после
попадания в эксперимент: `event_ts >= assigned_at`.

### ab.experiment_metrics_daily — витрина дневных метрик

Наполняется автоматически по закрытым виртуальным дням. Источник данных для
блока «Метрики по дням» в админке.

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `experiment_id` | UInt32 | Эксперимент |
| `experiment_code` | LowCardinality(String) | Его код |
| `metric_code` | LowCardinality(String) | Метрика из реестра |
| `metric_role` | LowCardinality(String) | `target` / `proxy` / `guardrail` |
| `date` | Date | Виртуальный день |
| `variant` | LowCardinality(String) | Группа |
| `numerator` | Float64 | Числитель метрики за день |
| `denominator` | Float64 | Знаменатель за день |
| `value` | Float64 | numerator / denominator |
| `computed_at` | DateTime | Служебное: реальное время расчёта |

Тоже ReplacingMergeTree — читай через `FINAL`. Метрику за период считай как
`sum(numerator) / sum(denominator)`, а не как среднее дневных `value`.

---

## PostgreSQL

### core.users — база пользователей

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `user_id` | BIGINT | Ключ |
| `region_code` | TEXT | Регион, ключ к `core.regions` |
| `platform` | TEXT | `ios` / `android` |
| `signup_date` | DATE | Дата регистрации |

150 тыс. строк. Часть базы — «спящие» пользователи: в таблице есть, в событиях
почти не появляются.

### core.user_stats — активность за предпериод

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `user_id` | BIGINT | Ключ |
| `sessions_preperiod` | INT | Сессий за 45 дней истории |
| `trips_preperiod` | INT | Поездок за 45 дней истории |

По этой таблице работает фильтр аудитории «минимум поездок в предпериоде».

### core.regions, core.tariffs — справочники

Регионы (6 штук, код и название) и тарифы (код, название, множитель цены).

### ab.experiments — конфигурация экспериментов

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `experiment_id` | SERIAL | Ключ |
| `code` | TEXT | Код эксперимента, попадает в витрины |
| `name`, `hypothesis`, `owner` | TEXT | Описание |
| `status` | TEXT | `draft` → `running` → `stopped`; остановленный не перезапускается |
| `salt` | TEXT | Соль детерминированного хеша — своя у каждого эксперимента |
| `variants` | JSONB | `[{"name": "A", "share": 50}, …]` — доли в % от всего трафика аудитории |
| `audience_filters` | JSONB | Регионы, платформы, минимум поездок |
| `start_virtual_ts`, `stop_virtual_ts` | TIMESTAMP | Виртуальные границы эксперимента |

### ab.assignments — абшница (source of truth)

Те же поля, что в реплике ClickHouse. Одна строка на пару (эксперимент,
пользователь), пишется при первом заходе пользователя в эксперимент.

### ab.metrics — реестр метрик

| Поле | Тип | Описание |
|:-----|:----|:---------|
| `metric_id` | SERIAL | Ключ |
| `code`, `name` | TEXT | Код и название |
| `kind` | TEXT | `proportion` / `mean` / `ratio` |
| `sql_template` | TEXT | SQL-шаблон по `ab.events` с плейсхолдерами `{experiment_id:UInt32}` и `{date:Date}` |
| `description` | TEXT | Что считает и когда применять |

### ab.experiment_metrics — метрики эксперимента

Связка: `experiment_id`, `metric_id`, `role` (`target` / `proxy` / `guardrail`).

### ab.metric_errors — журнал ошибок расчёта

`experiment_id`, `metric_code`, `date`, `error`, `failed_at`. Если SQL метрики
упал на каком-то дне, ошибка попадает сюда и показывается бейджем на странице
эксперимента; успешный пересчёт запись снимает.

### ab.sim_clock — виртуальные часы

Одна строка: `virtual_now` (текущий момент симуляции), `accel` (ускорение).
Это же время отдаёт `GET /sim/now` и показывает шапка админки.
