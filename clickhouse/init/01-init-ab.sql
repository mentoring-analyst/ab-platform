CREATE DATABASE IF NOT EXISTS ab;

-- Поток продуктовых событий. Всё аналитическое время — ВИРТУАЛЬНОЕ (event_ts).
-- inserted_at — реальное время вставки, только для отладки, в метриках не использовать.
CREATE TABLE IF NOT EXISTS ab.events
(
    event_date   Date,
    event_ts     DateTime,
    user_id      UInt64,
    session_id   String,
    event_name   LowCardinality(String),
    region       LowCardinality(String),
    platform     LowCardinality(String),
    tariff       LowCardinality(String),
    -- Что увидел пользователь на экране: диапазон (вариант A) или точечная оценка (B/C)
    price_low    Nullable(Float64),
    price_high   Nullable(Float64),
    price_shown  Nullable(Float64),
    -- Фактическая цена поездки (только для trip_complete)
    price_actual Nullable(Float64),
    inserted_at  DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, user_id, event_ts);

-- Реплика абшницы из Postgres (льёт Airflow DAG).
-- ReplacingMergeTree схлопывает дубли по ключу сортировки — повторные вставки безопасны.
CREATE TABLE IF NOT EXISTS ab.assignments
(
    experiment_id UInt32,
    user_id       UInt64,
    variant       LowCardinality(String),
    assigned_at   DateTime
)
ENGINE = ReplacingMergeTree
ORDER BY (experiment_id, user_id);

-- Витрина дневных метрик экспериментов (наполняет Airflow DAG).
-- Пересчёт дня = повторная вставка, ReplacingMergeTree(computed_at) оставит свежую версию.
CREATE TABLE IF NOT EXISTS ab.experiment_metrics_daily
(
    experiment_id UInt32,
    experiment_code LowCardinality(String),
    metric_code   LowCardinality(String),
    metric_role   LowCardinality(String),
    date          Date,
    variant       LowCardinality(String),
    numerator     Float64,
    denominator   Float64,
    value         Float64,
    computed_at   DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (experiment_id, metric_code, date, variant);
