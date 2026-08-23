import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, metrics_worker
from .routers import admin, experiments, metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = asyncio.create_task(metrics_worker.run_forever())
    yield
    worker.cancel()


app = FastAPI(
    title="AB Platform — Experiment Manager",
    description=(
        "Учебная сплитовалка Симулятора бигтеха: создание экспериментов, "
        "детерминированное сплитование, реестр метрик. Работать удобно прямо из /docs."
    ),
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/docs", include_in_schema=False)
def custom_docs():
    """Swagger UI с нашей темой: свой CSS добавляется поверх дефолтного."""
    html = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="AB Platform — консоль экспериментов",
        swagger_ui_parameters={
            "docExpansion": "list",
            "tryItOutEnabled": True,
            "displayRequestDuration": True,
        },
    ).body.decode()
    html = html.replace(
        "</head>", '<link rel="stylesheet" href="/static/swagger-theme.css"></head>'
    )
    return HTMLResponse(html)

app.include_router(experiments.router)
app.include_router(metrics.router)
app.include_router(admin.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sim/now", tags=["sim"])
def sim_now():
    """Текущее виртуальное время симуляции."""
    now = db.sim_now()
    return {"virtual_now": now, "initialized": now is not None}
