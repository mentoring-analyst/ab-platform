"""Фоновый расчёт метрик экспериментов.

В больших компаниях эту работу делает оркестратор (Airflow и т.п.): реплицирует
абшницу в аналитическое хранилище и по расписанию считает витрины. Масштаб
учебного стенда отдельного оркестратора не требует, поэтому здесь это фоновая
задача сплитовалки: каждые 45 секунд она

1) дореплицирует ab.assignments из Postgres в ClickHouse;
2) считает каждую прикреплённую метрику каждого активного эксперимента
   по каждому закрытому виртуальному дню и пишет в ab.experiment_metrics_daily.

Расчёт идемпотентен: уже посчитанные дни пропускаются, упавшие пишутся
в ab.metric_errors и ретраятся следующим циклом, не мешая остальным метрикам.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from . import ch, db

log = logging.getLogger("uvicorn.error")

CYCLE_SECONDS = 45

ASSIGNMENT_COLUMNS = ["experiment_id", "user_id", "variant", "assigned_at"]
MART_COLUMNS = [
    "experiment_id", "experiment_code", "metric_code", "metric_role",
    "date", "variant", "numerator", "denominator", "value",
]


def _sync_assignments(client) -> int:
    """Инкрементальная репликация абшницы: Postgres (source of truth) -> ClickHouse.
    Граница включительно — в одном виртуальном часе много назначений с одинаковым
    временем; дубли схлопнет ReplacingMergeTree."""
    last = client.query("SELECT max(assigned_at) FROM ab.assignments").result_rows[0][0]
    rows = db.fetch_all(
        """
        SELECT experiment_id, user_id, variant, assigned_at
        FROM ab.assignments
        WHERE assigned_at >= %(last)s
        """,
        {"last": last or datetime(1970, 1, 1)},
    )
    if rows:
        client.insert(
            "ab.assignments",
            [[r[col] for col in ASSIGNMENT_COLUMNS] for r in rows],
            column_names=ASSIGNMENT_COLUMNS,
        )
    return len(rows)


def _compute_metrics(client) -> int:
    now = db.sim_now()
    if now is None:
        return 0
    virtual_today = now.date()
    combos = db.fetch_all(
        """
        SELECT e.experiment_id, e.code, e.start_virtual_ts, e.stop_virtual_ts,
               m.code AS metric_code, m.sql_template, em.role
        FROM ab.experiments e
        JOIN ab.experiment_metrics em USING (experiment_id)
        JOIN ab.metrics m USING (metric_id)
        WHERE e.status IN ('running', 'stopped')
          AND e.start_virtual_ts IS NOT NULL
        """
    )
    if not combos:
        return 0

    computed_until = {
        (r[0], r[1]): r[2]
        for r in client.query(
            "SELECT experiment_id, metric_code, max(date) "
            "FROM ab.experiment_metrics_daily GROUP BY experiment_id, metric_code"
        ).result_rows
    }
    retry = {
        (r["experiment_id"], r["metric_code"], r["date"])
        for r in db.fetch_all("SELECT experiment_id, metric_code, date FROM ab.metric_errors")
    }

    written = 0
    for c in combos:
        day = c["start_virtual_ts"].date()
        end = virtual_today
        if c["stop_virtual_ts"]:
            end = min(end, c["stop_virtual_ts"].date() + timedelta(days=1))
        done_until = computed_until.get((c["experiment_id"], c["metric_code"]), date(1970, 1, 1))
        while day < end:
            if day <= done_until and (c["experiment_id"], c["metric_code"], day) not in retry:
                day += timedelta(days=1)
                continue
            try:
                res = client.query(
                    c["sql_template"],
                    parameters={"experiment_id": c["experiment_id"], "date": day},
                )
                cols = {name: i for i, name in enumerate(res.column_names)}
                out = [
                    [
                        c["experiment_id"], c["code"], c["metric_code"], c["role"], day,
                        r[cols["variant"]],
                        float(r[cols["numerator"]] or 0),
                        float(r[cols["denominator"]] or 0),
                        float(r[cols["value"]] or 0),
                    ]
                    for r in res.result_rows
                ]
                if out:
                    client.insert("ab.experiment_metrics_daily", out, column_names=MART_COLUMNS)
                    written += len(out)
                db.execute(
                    "DELETE FROM ab.metric_errors "
                    "WHERE experiment_id = %(e)s AND metric_code = %(m)s AND date = %(d)s",
                    {"e": c["experiment_id"], "m": c["metric_code"], "d": day},
                )
            except Exception as exc:
                db.execute(
                    "INSERT INTO ab.metric_errors (experiment_id, metric_code, date, error) "
                    "VALUES (%(e)s, %(m)s, %(d)s, %(err)s)",
                    {"e": c["experiment_id"], "m": c["metric_code"], "d": day,
                     "err": str(exc)[:2000]},
                )
            day += timedelta(days=1)
    return written


def _cycle():
    client = ch.client()
    synced = _sync_assignments(client)
    written = _compute_metrics(client)
    if synced or written:
        log.info(f"[metrics-worker] абшница: +{synced}, витрина: +{written} строк")


async def run_forever():
    log.info(f"[metrics-worker] запущен, цикл каждые {CYCLE_SECONDS} с")
    while True:
        try:
            await asyncio.to_thread(_cycle)
        except Exception as exc:
            log.warning(f"[metrics-worker] цикл упал, повторю: {exc!r}")
        await asyncio.sleep(CYCLE_SECONDS)
