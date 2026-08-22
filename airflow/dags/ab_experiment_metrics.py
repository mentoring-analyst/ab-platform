"""Платформенный DAG: реплицирует абшницу из Postgres в ClickHouse и наполняет
витрину дневных метрик экспериментов ab.experiment_metrics_daily.

Расписание идёт в РЕАЛЬНОМ времени (каждые 5 минут), а данные живут в ВИРТУАЛЬНОМ:
каждый запуск идемпотентно пересчитывает все закрытые виртуальные дни активных
экспериментов. Повторная вставка безопасна — ReplacingMergeTree(computed_at)
оставляет свежую версию строки. Поэтому catchup и завязки на {{ ds }} здесь
не нужны и вредны.
"""

import os
from datetime import datetime, timedelta

import clickhouse_connect
import psycopg2
import psycopg2.extras
from airflow.decorators import dag, task


def _pg():
    return psycopg2.connect(os.environ["PG_DSN"])


def _ch():
    return clickhouse_connect.get_client(
        host=os.environ["CH_HOST"],
        port=int(os.environ.get("CH_PORT", 8123)),
        username=os.environ.get("CH_USER", "default"),
        password=os.environ.get("CH_PASSWORD", ""),
    )


@dag(
    dag_id="ab_experiment_metrics",
    schedule="*/5 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["ab-platform"],
)
def ab_experiment_metrics():

    @task
    def sync_assignments() -> int:
        """Инкрементальная репликация ab.assignments: Postgres (source of truth) -> ClickHouse.

        Берём строки с assigned_at >= максимума в ClickHouse: граница включительно,
        потому что в одном виртуальном часе много назначений с одинаковым временем.
        Дубли схлопнет ReplacingMergeTree.
        """
        pg, ch = _pg(), _ch()
        last = ch.query("SELECT max(assigned_at) FROM ab.assignments").result_rows[0][0]
        with pg.cursor() as cur:
            cur.execute(
                """
                SELECT experiment_id, user_id, variant, assigned_at
                FROM ab.assignments
                WHERE assigned_at >= %s
                """,
                (last or datetime(1970, 1, 1),),
            )
            rows = cur.fetchall()
        if rows:
            ch.insert(
                "ab.assignments",
                rows,
                column_names=["experiment_id", "user_id", "variant", "assigned_at"],
            )
        return len(rows)

    @task
    def compute_metrics() -> int:
        """Считает каждую прикреплённую метрику каждого активного эксперимента
        по каждому закрытому виртуальному дню и пишет в витрину.

        Ошибки изолированы на уровне (эксперимент, метрика, день): упавший SQL
        одной метрики пишется в ab.metric_errors и не мешает остальным расчётам.
        Успешный пересчёт того же дня снимает запись об ошибке."""
        pg, ch = _pg(), _ch()
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT virtual_now FROM ab.sim_clock WHERE id = 1")
            row = cur.fetchone()
            if not row:
                return 0
            virtual_today = row["virtual_now"].date()
            cur.execute(
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
            combos = cur.fetchall()

        written = 0
        failed = 0
        err_cur = pg.cursor()
        for c in combos:
            day = c["start_virtual_ts"].date()
            end = virtual_today
            if c["stop_virtual_ts"]:
                end = min(end, c["stop_virtual_ts"].date() + timedelta(days=1))
            while day < end:
                try:
                    res = ch.query(
                        c["sql_template"],
                        parameters={"experiment_id": c["experiment_id"], "date": day},
                    )
                    cols = {name: i for i, name in enumerate(res.column_names)}
                    out = [
                        (
                            c["experiment_id"], c["code"], c["metric_code"], c["role"], day,
                            r[cols["variant"]],
                            float(r[cols["numerator"]] or 0),
                            float(r[cols["denominator"]] or 0),
                            float(r[cols["value"]] or 0),
                        )
                        for r in res.result_rows
                    ]
                    if out:
                        ch.insert(
                            "ab.experiment_metrics_daily",
                            out,
                            column_names=[
                                "experiment_id", "experiment_code", "metric_code", "metric_role",
                                "date", "variant", "numerator", "denominator", "value",
                            ],
                        )
                        written += len(out)
                    err_cur.execute(
                        "DELETE FROM ab.metric_errors "
                        "WHERE experiment_id = %s AND metric_code = %s AND date = %s",
                        (c["experiment_id"], c["metric_code"], day),
                    )
                except Exception as e:
                    failed += 1
                    err_cur.execute(
                        "INSERT INTO ab.metric_errors (experiment_id, metric_code, date, error) "
                        "VALUES (%s, %s, %s, %s)",
                        (c["experiment_id"], c["metric_code"], day, str(e)[:2000]),
                    )
                day += timedelta(days=1)
        pg.commit()
        print(f"[compute_metrics] строк в витрину: {written}, упавших расчётов: {failed}")
        return written

    sync_assignments() >> compute_metrics()


ab_experiment_metrics()
