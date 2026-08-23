from datetime import date

from fastapi import APIRouter, HTTPException

from .. import ch, db
from ..schemas import MetricCreate

router = APIRouter(prefix="/metrics", tags=["metrics"])

REQUIRED_PLACEHOLDERS = ["{experiment_id:UInt32}", "{date:Date}"]
REQUIRED_COLUMNS = {"variant", "numerator", "denominator", "value"}


def validate_metric_sql(sql_template: str) -> None:
    """Проверка SQL метрики ДО сохранения: плейсхолдеры, сухой прогон в ClickHouse
    (ловит синтаксические ошибки и опечатки в именах таблиц/колонок), контракт колонок."""
    missing = [p for p in REQUIRED_PLACEHOLDERS if p not in sql_template]
    if missing:
        raise HTTPException(
            422,
            f"в sql_template нет обязательных плейсхолдеров: {missing}. "
            "Метрика — это шаблон: DAG выполняет её для каждого эксперимента и каждого "
            "закрытого виртуального дня, подставляя значения в плейсхолдеры. Каркас: "
            "SELECT a.variant AS variant, <числитель> AS numerator, <знаменатель> AS denominator, "
            "numerator/denominator AS value "
            "FROM ab.events e INNER JOIN (SELECT user_id, variant, assigned_at "
            "FROM ab.assignments FINAL WHERE experiment_id = {experiment_id:UInt32}) a "
            "ON a.user_id = e.user_id "
            "WHERE e.event_date = {date:Date} AND e.event_ts >= a.assigned_at "
            "GROUP BY a.variant",
        )
    params = {"experiment_id": 0, "date": date(2000, 1, 1)}
    try:
        res = ch.client().query(sql_template, parameters=params)
        columns = set(res.column_names)
        if not columns:
            # при пустом результате нативный протокол не присылает структуру —
            # узнаём колонки через DESCRIBE
            desc = ch.client().query(
                f"DESCRIBE ({sql_template.rstrip().rstrip(';')})", parameters=params
            )
            columns = {row[0] for row in desc.result_rows}
    except Exception as e:
        raise HTTPException(422, f"SQL не прошёл проверку в ClickHouse: {str(e)[:600]}")
    missing_cols = REQUIRED_COLUMNS - columns
    if missing_cols:
        raise HTTPException(
            422,
            f"запрос обязан вернуть колонки {sorted(REQUIRED_COLUMNS)}; "
            f"не хватает: {sorted(missing_cols)}",
        )


@router.get("")
def list_metrics():
    return db.fetch_all("SELECT * FROM ab.metrics ORDER BY metric_id")


@router.post("", status_code=201)
def create_metric(body: MetricCreate):
    if db.fetch_one("SELECT 1 FROM ab.metrics WHERE code = %(c)s", {"c": body.code}):
        raise HTTPException(409, f"метрика '{body.code}' уже существует")
    validate_metric_sql(body.sql_template)
    return db.fetch_one(
        """
        INSERT INTO ab.metrics (code, name, kind, sql_template, description)
        VALUES (%(code)s, %(name)s, %(kind)s, %(sql)s, %(descr)s)
        RETURNING *
        """,
        {
            "code": body.code,
            "name": body.name,
            "kind": body.kind,
            "sql": body.sql_template,
            "descr": body.description,
        },
    )
