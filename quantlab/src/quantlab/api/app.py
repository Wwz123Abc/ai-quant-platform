"""The FastAPI application: routes, error mapping, security headers, request logging and the web workbench."""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..ai.jobs import Worker
from ..config import get_settings
from ..errors import QuantLabError
from .holdings_routes import router as holdings_router
from .ml_routes import router as ml_router
from .routes import api, public

log = logging.getLogger("quantlab.api")
WEB = Path(__file__).resolve().parent.parent / "web"  # the built React app (npm run build writes it here)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def create_app(worker=None, scheduler=None):
    """The application. The AI worker and the daily scheduler start with it and stop when it shuts down;
    tests can pass their own."""
    from ..scheduler import Scheduler

    worker = worker or Worker()
    scheduler = scheduler or Scheduler(worker)

    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        if get_settings().schedule_enabled:
            scheduler.start()
        yield
        scheduler.stop()
        worker.stop()

    app = FastAPI(title="QuantLab API", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan,
                  openapi_url="/api/openapi.json",
                  description="回测、参数扫描、每日信号和模拟盘。除 /api/health 外，所有接口都需要 `Authorization: Bearer <令牌>`。")

    @app.exception_handler(QuantLabError)
    async def domain_error(request: Request, exc: QuantLabError):
        if exc.status >= 500:
            log.warning("%s %s failed: %s", request.method, request.url.path, exc)
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("%s %s crashed", request.method, request.url.path)
        return JSONResponse({"detail": "服务器内部错误，详情见服务日志"}, status_code=500)

    @app.middleware("http")
    async def headers_and_log(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"  # the start-up link carries the token
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/api/docs"):  # the docs page loads Swagger UI from a CDN
            response.headers["Content-Security-Policy"] = CSP
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            log.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, (time.perf_counter() - t0) * 1000)
        return response

    app.state.worker, app.state.scheduler = worker, scheduler

    origins = get_settings().cors_origins
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    app.include_router(public)
    app.include_router(api)
    app.include_router(ml_router)
    app.include_router(holdings_router)

    if (WEB / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "没有这个接口"}, status_code=404)
            f = (WEB / path).resolve()
            if path and f.is_file() and f.is_relative_to(WEB):
                return FileResponse(f)
            return FileResponse(WEB / "index.html")  # client-side routes
    return app
