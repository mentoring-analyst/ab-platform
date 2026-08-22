import json
import uuid

from fastapi import APIRouter, HTTPException
from psycopg2.extras import execute_values

from .. import db
from ..hashing import bucket, pick_variant
from ..schemas import AttachMetric, BatchAssignRequest, ExperimentCreate, ExperimentUpdate

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _get_experiment(code: str):
    exp = db.fetch_one("SELECT * FROM ab.experiments WHERE code = %(code)s", {"code": code})
    if not exp:
        raise HTTPException(404, f"эксперимент '{code}' не найден")
    return exp


def _audience_where(filters: dict):
    clauses, params = [], {}
    if filters.get("regions"):
        clauses.append("u.region_code = ANY(%(regions)s)")
        params["regions"] = filters["regions"]
    if filters.get("platforms"):
        clauses.append("u.platform = ANY(%(platforms)s)")
        params["platforms"] = filters["platforms"]
    if filters.get("min_trips_preperiod"):
        clauses.append("st.trips_preperiod >= %(min_trips)s")
        params["min_trips"] = filters["min_trips_preperiod"]
    return " AND ".join(clauses) if clauses else "TRUE", params


@router.post("", status_code=201)
def create_experiment(body: ExperimentCreate):
    if db.fetch_one("SELECT 1 FROM ab.experiments WHERE code = %(c)s", {"c": body.code}):
        raise HTTPException(409, f"эксперимент '{body.code}' уже существует")
    unknown = set(body.audience_filters.regions or []) - {
        r["region_code"] for r in db.fetch_all("SELECT region_code FROM core.regions")
    }
    if unknown:
        raise HTTPException(422, f"неизвестные регионы: {sorted(unknown)}")
    row = db.fetch_one(
        """
        INSERT INTO ab.experiments (code, name, hypothesis, owner, salt, variants, audience_filters)
        VALUES (%(code)s, %(name)s, %(hypothesis)s, %(owner)s, %(salt)s, %(variants)s, %(filters)s)
        RETURNING *
        """,
        {
            "code": body.code,
            "name": body.name,
            "hypothesis": body.hypothesis,
            "owner": body.owner,
            "salt": uuid.uuid4().hex,
            "variants": json.dumps([v.model_dump() for v in body.variants]),
            "filters": json.dumps(body.audience_filters.model_dump()),
        },
    )
    return row


@router.get("")
def list_experiments(status: str | None = None):
    if status:
        return db.fetch_all(
            "SELECT * FROM ab.experiments WHERE status = %(s)s ORDER BY experiment_id", {"s": status}
        )
    return db.fetch_all("SELECT * FROM ab.experiments ORDER BY experiment_id")


@router.patch("/{code}")
def update_experiment(code: str, body: ExperimentUpdate):
    exp = _get_experiment(code)
    updates = {}
    for field in ("name", "hypothesis", "owner"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value.strip()
    if body.variants is not None or body.audience_filters is not None:
        if exp["status"] != "draft":
            raise HTTPException(
                409,
                "варианты и аудиторию можно менять только у черновика: после запуска "
                "это сломало бы сплит и сравнимость групп",
            )
        if body.variants is not None:
            updates["variants"] = json.dumps([v.model_dump() for v in body.variants])
        if body.audience_filters is not None:
            unknown = set(body.audience_filters.regions or []) - {
                r["region_code"] for r in db.fetch_all("SELECT region_code FROM core.regions")
            }
            if unknown:
                raise HTTPException(422, f"неизвестные регионы: {sorted(unknown)}")
            updates["audience_filters"] = json.dumps(body.audience_filters.model_dump())
    if not updates:
        return exp
    set_clause = ", ".join(f"{k} = %({k})s" for k in updates)
    return db.fetch_one(
        f"UPDATE ab.experiments SET {set_clause} WHERE experiment_id = %(id)s RETURNING *",
        {**updates, "id": exp["experiment_id"]},
    )


@router.get("/{code}")
def get_experiment(code: str):
    exp = _get_experiment(code)
    exp["metrics"] = db.fetch_all(
        """
        SELECT m.code, m.name, m.kind, em.role
        FROM ab.experiment_metrics em
        JOIN ab.metrics m USING (metric_id)
        WHERE em.experiment_id = %(id)s
        """,
        {"id": exp["experiment_id"]},
    )
    return exp


@router.post("/{code}/metrics")
def attach_metric(code: str, body: AttachMetric):
    exp = _get_experiment(code)
    metric = db.fetch_one("SELECT * FROM ab.metrics WHERE code = %(c)s", {"c": body.metric_code})
    if not metric:
        raise HTTPException(404, f"метрика '{body.metric_code}' не найдена в реестре")
    db.execute(
        """
        INSERT INTO ab.experiment_metrics (experiment_id, metric_id, role)
        VALUES (%(e)s, %(m)s, %(r)s)
        ON CONFLICT (experiment_id, metric_id) DO UPDATE SET role = EXCLUDED.role
        """,
        {"e": exp["experiment_id"], "m": metric["metric_id"], "r": body.role},
    )
    return {"experiment": code, "metric": body.metric_code, "role": body.role}


@router.delete("/{code}/metrics/{metric_code}")
def detach_metric(code: str, metric_code: str):
    exp = _get_experiment(code)
    db.execute(
        """
        DELETE FROM ab.experiment_metrics
        WHERE experiment_id = %(e)s
          AND metric_id = (SELECT metric_id FROM ab.metrics WHERE code = %(m)s)
        """,
        {"e": exp["experiment_id"], "m": metric_code},
    )
    return {"experiment": code, "metric": metric_code, "detached": True}


@router.post("/{code}/audience/preview")
def audience_preview(code: str):
    exp = _get_experiment(code)
    where, params = _audience_where(exp["audience_filters"])
    rows = db.fetch_all(
        f"""
        SELECT u.region_code, count(*) AS users
        FROM core.users u
        JOIN core.user_stats st USING (user_id)
        WHERE {where}
        GROUP BY u.region_code
        ORDER BY u.region_code
        """,
        params,
    )
    return {
        "experiment": code,
        "total_users": sum(r["users"] for r in rows),
        "by_region": {r["region_code"]: r["users"] for r in rows},
        "note": "Это размер аудитории под фильтрами. Реальный набор идёт постепенно — "
                "пользователь попадает в эксперимент при первом заходе на экран (exposure).",
    }


@router.post("/{code}/start")
def start_experiment(code: str):
    exp = _get_experiment(code)
    if exp["status"] == "running":
        raise HTTPException(409, "эксперимент уже запущен")
    if exp["status"] == "stopped":
        raise HTTPException(409, "остановленный эксперимент нельзя перезапустить — заведи новый")
    has_target = db.fetch_one(
        "SELECT 1 FROM ab.experiment_metrics WHERE experiment_id = %(id)s AND role = 'target'",
        {"id": exp["experiment_id"]},
    )
    if not has_target:
        raise HTTPException(422, "нельзя запустить эксперимент без целевой метрики: "
                                 "прикрепи метрику с role='target' через POST /experiments/{code}/metrics")
    now = db.sim_now()
    if now is None:
        raise HTTPException(503, "симуляция ещё не инициализирована (генератор не построил историю)")
    db.execute(
        "UPDATE ab.experiments SET status = 'running', start_virtual_ts = %(now)s WHERE experiment_id = %(id)s",
        {"now": now, "id": exp["experiment_id"]},
    )
    return {"experiment": code, "status": "running", "start_virtual_ts": now}


@router.post("/{code}/stop")
def stop_experiment(code: str):
    exp = _get_experiment(code)
    if exp["status"] != "running":
        raise HTTPException(409, f"эксперимент в статусе '{exp['status']}', остановить можно только running")
    now = db.sim_now()
    db.execute(
        "UPDATE ab.experiments SET status = 'stopped', stop_virtual_ts = %(now)s WHERE experiment_id = %(id)s",
        {"now": now, "id": exp["experiment_id"]},
    )
    return {"experiment": code, "status": "stopped", "stop_virtual_ts": now}


@router.get("/{code}/variant")
def get_variant(code: str, user_id: int):
    """Основной эндпоинт сплитования: детерминированный вариант для пользователя."""
    exp = _get_experiment(code)
    if exp["status"] != "running":
        raise HTTPException(409, f"эксперимент в статусе '{exp['status']}'")
    where, params = _audience_where(exp["audience_filters"])
    eligible = db.fetch_one(
        f"""
        SELECT u.user_id
        FROM core.users u
        JOIN core.user_stats st USING (user_id)
        WHERE u.user_id = %(uid)s AND {where}
        """,
        {**params, "uid": user_id},
    )
    if not eligible:
        return {"user_id": user_id, "variant": None, "reason": "not_in_audience"}
    b = bucket(exp["salt"], user_id)
    variant = pick_variant(exp["variants"], b)
    if variant is None:
        return {"user_id": user_id, "variant": None, "reason": "not_in_experiment", "bucket": b}
    db.execute(
        """
        INSERT INTO ab.assignments (experiment_id, user_id, variant, assigned_at)
        VALUES (%(e)s, %(u)s, %(v)s, %(ts)s)
        ON CONFLICT (experiment_id, user_id) DO NOTHING
        """,
        {"e": exp["experiment_id"], "u": user_id, "v": variant, "ts": db.sim_now()},
    )
    return {"user_id": user_id, "variant": variant, "bucket": b}


@router.post("/{code}/variants/batch")
def batch_variants(code: str, body: BatchAssignRequest):
    """Батчевое сплитование — используется генератором трафика (1 вызов на тик)."""
    exp = _get_experiment(code)
    if exp["status"] != "running":
        raise HTTPException(409, f"эксперимент в статусе '{exp['status']}'")
    where, params = _audience_where(exp["audience_filters"])
    eligible = db.fetch_all(
        f"""
        SELECT u.user_id
        FROM core.users u
        JOIN core.user_stats st USING (user_id)
        WHERE u.user_id = ANY(%(uids)s) AND {where}
        """,
        {**params, "uids": body.user_ids},
    )
    ts = body.virtual_ts or db.sim_now()
    assignments = {}
    not_in_experiment = []
    rows = []
    for r in eligible:
        uid = r["user_id"]
        variant = pick_variant(exp["variants"], bucket(exp["salt"], uid))
        if variant is None:
            not_in_experiment.append(uid)
            continue
        assignments[uid] = variant
        rows.append((exp["experiment_id"], uid, variant, ts))
    if rows:
        with db.conn() as c:
            with c.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO ab.assignments (experiment_id, user_id, variant, assigned_at)
                    VALUES %s
                    ON CONFLICT (experiment_id, user_id) DO NOTHING
                    """,
                    rows,
                )
    return {
        "assignments": assignments,
        "not_in_experiment": sorted(not_in_experiment),
        "not_in_audience": sorted(
            set(body.user_ids) - set(assignments) - set(not_in_experiment)
        ),
    }


@router.get("/{code}/stats")
def experiment_stats(code: str):
    """Быстрая сверка набора аудитории по вариантам (полный дашборд — в Superset)."""
    exp = _get_experiment(code)
    rows = db.fetch_all(
        """
        SELECT variant, count(*) AS users,
               min(assigned_at) AS first_exposure, max(assigned_at) AS last_exposure
        FROM ab.assignments
        WHERE experiment_id = %(id)s
        GROUP BY variant
        ORDER BY variant
        """,
        {"id": exp["experiment_id"]},
    )
    return {"experiment": code, "status": exp["status"], "variants": rows}
