"""HTML-админка: тонкий слой поверх тех же функций, что и API.

Никакой своей логики — формы собирают те же pydantic-модели и зовут
те же обработчики, что и Swagger. Один источник правды."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .. import ch, db
from ..schemas import (
    AttachMetric,
    AudienceFilters,
    ExperimentCreate,
    ExperimentUpdate,
    MetricCreate,
    VariantDef,
)
from . import experiments as api
from . import metrics as api_metrics

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/admin", include_in_schema=False)

STATUS_RU = {"draft": "черновик", "running": "идёт", "stopped": "остановлен"}
ROLE_RU = {"target": "целевая", "proxy": "прокси", "guardrail": "защитная"}
COLORS = ["#4f46e5", "#0ea5e9", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6"]


def _ctx(request: Request, **kw):
    return {"request": request, "sim_now": db.sim_now(), "status_ru": STATUS_RU,
            "role_ru": ROLE_RU, "colors": COLORS, "error": request.query_params.get("error"),
            "ok": request.query_params.get("ok"), **kw}


def _err_redirect(url: str, e: Exception) -> RedirectResponse:
    if isinstance(e, HTTPException):
        msg = str(e.detail)
    elif isinstance(e, ValidationError):
        msg = "; ".join(err["msg"] for err in e.errors())
    else:
        msg = str(e)
    return RedirectResponse(f"{url}?error={quote(msg)}", status_code=303)


def _form_variants(form) -> list[VariantDef]:
    return [
        VariantDef(name=str(n).strip(), share=int(s or 0))
        for n, s in zip(form.getlist("variant_name"), form.getlist("variant_share"))
        if str(n).strip()
    ]


def _form_filters(form) -> AudienceFilters:
    return AudienceFilters(
        regions=[str(r) for r in form.getlist("regions")] or None,
        platforms=[str(p) for p in form.getlist("platforms")] or None,
        min_trips_preperiod=int(form.get("min_trips_preperiod") or 0),
    )


def _form_dicts():
    return {
        "regions": db.fetch_all("SELECT * FROM core.regions ORDER BY region_code"),
        "metrics": db.fetch_all("SELECT * FROM ab.metrics ORDER BY metric_id"),
    }


@router.get("")
def index(request: Request):
    experiments = db.fetch_all("SELECT * FROM ab.experiments ORDER BY experiment_id DESC")
    counts = {
        r["experiment_id"]: r["users"]
        for r in db.fetch_all(
            "SELECT experiment_id, count(*) AS users FROM ab.assignments GROUP BY experiment_id"
        )
    }
    return templates.TemplateResponse(
        request, "index.html", _ctx(request, experiments=experiments, counts=counts)
    )


# ---------- создание ----------

@router.get("/new")
def new_form(request: Request):
    return templates.TemplateResponse(
        request, "exp_form.html",
        _ctx(request, mode="new", exp=None, attached={}, **_form_dicts()),
    )


@router.post("/experiments")
async def create_from_form(request: Request):
    form = await request.form()
    code = str(form.get("code", "")).strip()
    try:
        body = ExperimentCreate(
            code=code,
            name=str(form.get("name", "")).strip(),
            hypothesis=str(form.get("hypothesis", "")).strip(),
            owner=str(form.get("owner", "")).strip(),
            variants=_form_variants(form),
            audience_filters=_form_filters(form),
        )
        api.create_experiment(body)
        for mc in form.getlist("metric_codes"):
            role = str(form.get(f"role_{mc}", "proxy"))
            api.attach_metric(code, AttachMetric(metric_code=str(mc), role=role))
    except (HTTPException, ValidationError, ValueError) as e:
        return _err_redirect("/admin/new", e)
    return RedirectResponse(f"/admin/experiments/{code}", status_code=303)


# ---------- редактирование ----------

@router.get("/experiments/{code}/edit")
def edit_form(request: Request, code: str):
    exp = api.get_experiment(code)
    attached = {m["code"]: m["role"] for m in exp["metrics"]}
    return templates.TemplateResponse(
        request, "exp_form.html",
        _ctx(request, mode="edit", exp=exp, attached=attached, **_form_dicts()),
    )


@router.post("/experiments/{code}/edit")
async def edit_from_form(request: Request, code: str):
    form = await request.form()
    try:
        exp = api.get_experiment(code)
        draft = exp["status"] == "draft"
        body = ExperimentUpdate(
            name=str(form.get("name", "")).strip() or None,
            hypothesis=str(form.get("hypothesis", "")).strip(),
            owner=str(form.get("owner", "")).strip(),
            variants=_form_variants(form) if draft else None,
            audience_filters=_form_filters(form) if draft else None,
        )
        api.update_experiment(code, body)
        if draft:
            selected = {str(mc) for mc in form.getlist("metric_codes")}
            current = {m["code"] for m in exp["metrics"]}
            for mc in selected:
                role = str(form.get(f"role_{mc}", "proxy"))
                api.attach_metric(code, AttachMetric(metric_code=mc, role=role))
            for mc in current - selected:
                api.detach_metric(code, mc)
    except (HTTPException, ValidationError, ValueError) as e:
        return _err_redirect(f"/admin/experiments/{code}/edit", e)
    return RedirectResponse(f"/admin/experiments/{code}", status_code=303)


# ---------- карточка эксперимента ----------

def _fmt_value(v) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}".replace(",", " ") if abs(v) >= 100 else f"{v:.4f}"


def _metrics_daily(exp: dict) -> list[dict]:
    """Дневные значения метрик эксперимента из витрины ClickHouse,
    сгруппированные для отрисовки: метрика -> дни -> значения по вариантам."""
    try:
        rows = ch.client().query(
            """
            SELECT metric_code, date, variant, value
            FROM ab.experiment_metrics_daily FINAL
            WHERE experiment_id = {id:UInt32}
            ORDER BY metric_code, date
            """,
            parameters={"id": exp["experiment_id"]},
        ).result_rows
    except Exception:
        return []
    meta = {m["code"]: m for m in exp["metrics"]}
    grouped: dict[str, dict] = {}
    for code, d, variant, value in rows:
        m = grouped.setdefault(code, {
            "name": meta.get(code, {}).get("name", code),
            "role": meta.get(code, {}).get("role", "proxy"),
            "days": {},
        })
        m["days"].setdefault(d, {})[variant] = _fmt_value(value)
    result = []
    for m in grouped.values():
        m["days"] = sorted(m["days"].items(), reverse=True)
        result.append(m)
    return result


@router.get("/experiments/{code}")
def detail(request: Request, code: str):
    exp = api.get_experiment(code)
    preview = api.audience_preview(code)
    plan_shares = {v["name"]: v["share"] for v in exp["variants"]}
    exposure = sum(v["share"] for v in exp["variants"])
    metric_errors = {
        r["metric_code"]: r
        for r in db.fetch_all(
            """
            SELECT DISTINCT ON (metric_code) metric_code, date, error, failed_at
            FROM ab.metric_errors
            WHERE experiment_id = %(id)s
            ORDER BY metric_code, failed_at DESC
            """,
            {"id": exp["experiment_id"]},
        )
    }
    return templates.TemplateResponse(
        request, "detail.html",
        _ctx(request, exp=exp, preview=preview, plan_shares=plan_shares,
             exposure=exposure, metric_errors=metric_errors,
             metrics_daily=_metrics_daily(exp),
             variant_names=[v["name"] for v in exp["variants"]]),
    )


@router.post("/experiments/{code}/start")
def start_from_form(code: str):
    try:
        api.start_experiment(code)
    except HTTPException as e:
        return _err_redirect(f"/admin/experiments/{code}", e)
    return RedirectResponse(f"/admin/experiments/{code}", status_code=303)


@router.post("/experiments/{code}/stop")
def stop_from_form(code: str):
    try:
        api.stop_experiment(code)
    except HTTPException as e:
        return _err_redirect(f"/admin/experiments/{code}", e)
    return RedirectResponse(f"/admin/experiments/{code}", status_code=303)


# ---------- реестр метрик ----------

@router.get("/metrics")
def metrics_page(request: Request):
    metrics = db.fetch_all("SELECT * FROM ab.metrics ORDER BY metric_id")
    return templates.TemplateResponse(
        request, "metrics.html", _ctx(request, metrics=metrics)
    )


@router.post("/metrics")
async def create_metric_from_form(request: Request):
    form = await request.form()
    try:
        api_metrics.create_metric(MetricCreate(
            code=str(form.get("code", "")).strip(),
            name=str(form.get("name", "")).strip(),
            kind=str(form.get("kind", "proportion")),
            sql_template=str(form.get("sql_template", "")),
            description=str(form.get("description", "")).strip(),
        ))
    except (HTTPException, ValidationError, ValueError) as e:
        return _err_redirect("/admin/metrics", e)
    return RedirectResponse("/admin/metrics?ok=1", status_code=303)
