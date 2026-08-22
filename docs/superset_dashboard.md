# Дашборд эксперимента в Superset

Это дашборд, который смотрит продакт, пока идёт эксперимент. Собери его сам —
ниже подключения, SQL для каждого чарта и чек-лист.

## Подключения (Settings → Database Connections → + Database)

| База | SQLAlchemy URI |
|:-----|:---------------|
| Postgres (абшница, часы) | `postgresql+psycopg2://platform:platform@postgres:5432/platform` |
| ClickHouse (события, витрины) | `clickhousedb://default:platform@clickhouse:8123/ab` |

Важно: Superset живёт внутри docker-сети, поэтому хосты — `postgres` и `clickhouse`, не `localhost`.

## Важно про время

Все данные живут в **виртуальном** времени. Относительные фильтры Superset
(«Last 7 days») считаются от реального времени и покажут пустоту — используй
явные диапазоны дат или без фильтра по времени вовсе.

## Чарт 0. «Сегодня» в симуляции (Big Number, Postgres)

```sql
SELECT virtual_now FROM ab.sim_clock WHERE id = 1
```

## Чарт 1. Кривая набора аудитории (Line Chart, Postgres)

Накопительное число пользователей в каждой группе по виртуальным дням.
По этой кривой продакт видит, когда наберётся расчётная выборка из карточки.

```sql
SELECT
    day,
    variant,
    sum(users) OVER (PARTITION BY variant ORDER BY day) AS cumulative_users
FROM (
    SELECT assigned_at::date AS day, variant, count(*) AS users
    FROM ab.assignments a
    JOIN ab.experiments e USING (experiment_id)
    WHERE e.code = 'pricing_point_estimate'
    GROUP BY 1, 2
) t
ORDER BY day
```

## Чарт 2. SRM-проверка (Table, Postgres)

SRM (Sample Ratio Mismatch) — расхождение фактических долей групп с плановыми.
Главный sanity-check: если доли уехали, сплит сломан и метрики можно не смотреть.

```sql
SELECT
    variant,
    count(*) AS users,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS actual_share_pct
FROM ab.assignments a
JOIN ab.experiments e USING (experiment_id)
WHERE e.code = 'pricing_point_estimate'
GROUP BY variant
ORDER BY variant
```

Формальный тест (хи-квадрат) посчитай в ноутбуке анализа; на дашборде достаточно
таблицы с фактическими долями рядом с плановыми.

## Чарт 3. Динамика метрик по дням (Line Chart, ClickHouse)

Витрину наполняет DAG `ab_experiment_metrics` — включи его в Airflow.

```sql
SELECT date, variant, metric_code, value
FROM ab.experiment_metrics_daily FINAL
WHERE experiment_code = 'pricing_point_estimate'
ORDER BY date
```

Сделай отдельный чарт на каждую метрику (фильтр по `metric_code`): целевая,
прокси, защитные. Защитные — на видное место: деградация там важнее роста целевой.

## Чек-лист готового дашборда

- [ ] Виден текущий виртуальный день
- [ ] Кривая набора аудитории по вариантам + горизонтальная отметка расчётной выборки
- [ ] Фактические доли групп рядом с плановыми
- [ ] Целевая метрика и защитные метрики по дням
- [ ] Все заголовки — человеческим языком (продакт не обязан знать слово SRM)
