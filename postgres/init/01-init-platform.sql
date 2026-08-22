-- Схема core: справочники и пользователи (OLTP-мир продукта)
CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE core.regions (
    region_code TEXT PRIMARY KEY,
    region_name TEXT NOT NULL
);

INSERT INTO core.regions (region_code, region_name) VALUES
    ('manhattan',     'Manhattan'),
    ('brooklyn',      'Brooklyn'),
    ('queens',        'Queens'),
    ('bronx',         'Bronx'),
    ('staten_island', 'Staten Island'),
    ('jersey_city',   'Jersey City');

CREATE TABLE core.tariffs (
    tariff_code TEXT PRIMARY KEY,
    tariff_name TEXT NOT NULL,
    price_multiplier NUMERIC NOT NULL
);

INSERT INTO core.tariffs (tariff_code, tariff_name, price_multiplier) VALUES
    ('uberx',   'UberX',   1.00),
    ('comfort', 'Comfort', 1.35),
    ('uberxl',  'UberXL',  1.25);

-- Заполняется генератором при первом старте (bootstrap популяции)
CREATE TABLE core.users (
    user_id BIGINT PRIMARY KEY,
    region_code TEXT NOT NULL REFERENCES core.regions (region_code),
    platform TEXT NOT NULL CHECK (platform IN ('ios', 'android')),
    signup_date DATE NOT NULL
);

CREATE INDEX idx_users_region ON core.users (region_code);

-- Статистика предпериода: заполняется генератором после бэкфилла истории.
-- Нужна сплитовалке для фильтров аудитории (audience/preview) без похода в ClickHouse.
CREATE TABLE core.user_stats (
    user_id BIGINT PRIMARY KEY REFERENCES core.users (user_id),
    sessions_preperiod INT NOT NULL DEFAULT 0,
    trips_preperiod INT NOT NULL DEFAULT 0
);

-- Схема ab: конфигурация экспериментов (source of truth — здесь, в OLTP;
-- в ClickHouse ассайнменты реплицируются DAG-ом для join-ов с событиями)
CREATE SCHEMA IF NOT EXISTS ab;

CREATE TABLE ab.experiments (
    experiment_id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'running', 'stopped')),
    salt TEXT NOT NULL,
    -- [{"name": "A", "share": 50}, {"name": "B", "share": 50}] — доли в процентах
    variants JSONB NOT NULL,
    -- {"regions": ["manhattan"], "platforms": ["ios","android"], "min_trips_preperiod": 1}
    audience_filters JSONB NOT NULL DEFAULT '{}',
    start_virtual_ts TIMESTAMP,
    stop_virtual_ts TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Реестр метрик: SQL-шаблоны считаются DAG-ом в ClickHouse.
-- Контракт шаблона: обязан вернуть колонки (variant, numerator, denominator, value),
-- доступны плейсхолдеры {experiment_id:UInt32} и {date:Date}.
CREATE TABLE ab.metrics (
    metric_id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('proportion', 'mean', 'ratio')),
    sql_template TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE ab.experiment_metrics (
    experiment_id INT NOT NULL REFERENCES ab.experiments (experiment_id),
    metric_id INT NOT NULL REFERENCES ab.metrics (metric_id),
    role TEXT NOT NULL CHECK (role IN ('target', 'proxy', 'guardrail')),
    PRIMARY KEY (experiment_id, metric_id)
);

-- Абшница: одна строка на пользователя в эксперименте, момент exposure — виртуальный
CREATE TABLE ab.assignments (
    experiment_id INT NOT NULL REFERENCES ab.experiments (experiment_id),
    user_id BIGINT NOT NULL,
    variant TEXT NOT NULL,
    assigned_at TIMESTAMP NOT NULL,
    PRIMARY KEY (experiment_id, user_id)
);

CREATE INDEX idx_assignments_assigned_at ON ab.assignments (experiment_id, assigned_at);

-- Журнал ошибок расчёта метрик: пишет Airflow DAG, если SQL метрики упал.
-- Успешный пересчёт того же дня удаляет запись.
CREATE TABLE ab.metric_errors (
    experiment_id INT NOT NULL,
    metric_code TEXT NOT NULL,
    date DATE NOT NULL,
    error TEXT NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_metric_errors ON ab.metric_errors (experiment_id, metric_code, failed_at DESC);

-- Виртуальные часы симуляции: единственная строка, обновляет генератор
CREATE TABLE ab.sim_clock (
    id INT PRIMARY KEY CHECK (id = 1),
    virtual_now TIMESTAMP NOT NULL,
    accel NUMERIC NOT NULL,
    updated_real_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Стартовые метрики реестра (студенты добавляют свои через API)
INSERT INTO ab.metrics (code, name, kind, sql_template, description) VALUES
(
    'conversion_view_to_order',
    'Конверсия просмотра экрана в заказ',
    'proportion',
    $sql$
SELECT
    a.variant AS variant,
    uniqExactIf(e.user_id, e.event_name = 'order_confirm') AS numerator,
    uniqExactIf(e.user_id, e.event_name = 'screen_view')   AS denominator,
    numerator / denominator AS value
FROM ab.events AS e
INNER JOIN
(
    SELECT experiment_id, user_id, variant, assigned_at
    FROM ab.assignments FINAL
    WHERE experiment_id = {experiment_id:UInt32}
) AS a ON a.user_id = e.user_id
WHERE e.event_date = {date:Date}
  AND e.event_ts >= a.assigned_at
GROUP BY a.variant
$sql$,
    'Доля пользователей, дошедших от просмотра экрана выбора до подтверждения заказа (user-level, за день)'
),
(
    'cancel_rate',
    'Доля отменённых заказов',
    'ratio',
    $sql$
SELECT
    a.variant AS variant,
    countIf(e.event_name = 'order_cancelled') AS numerator,
    countIf(e.event_name = 'order_confirm')   AS denominator,
    numerator / denominator AS value
FROM ab.events AS e
INNER JOIN
(
    SELECT experiment_id, user_id, variant, assigned_at
    FROM ab.assignments FINAL
    WHERE experiment_id = {experiment_id:UInt32}
) AS a ON a.user_id = e.user_id
WHERE e.event_date = {date:Date}
  AND e.event_ts >= a.assigned_at
GROUP BY a.variant
$sql$,
    'Отмены на заказ — защитная метрика. Тип ratio: юнит анализа (заказ) мельче юнита рандомизации (пользователь), для стат. теста нужен дельта-метод или линеаризация'
),
(
    'trips_per_user',
    'Поездок на пользователя',
    'mean',
    $sql$
SELECT
    a.variant AS variant,
    countIf(e.event_name = 'trip_complete') AS numerator,
    uniqExact(e.user_id)                    AS denominator,
    numerator / denominator AS value
FROM ab.events AS e
INNER JOIN
(
    SELECT experiment_id, user_id, variant, assigned_at
    FROM ab.assignments FINAL
    WHERE experiment_id = {experiment_id:UInt32}
) AS a ON a.user_id = e.user_id
WHERE e.event_date = {date:Date}
  AND e.event_ts >= a.assigned_at
GROUP BY a.variant
$sql$,
    'Среднее число завершённых поездок на пользователя, попавшего в эксперимент (за день)'
);
