from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_302_FOUND

from dzen_commenter.admin import auth
from dzen_commenter.admin.auth import BASE_DIR, NotAuthenticated, require_login, templates
from dzen_commenter.admin.config import AdminSettings
from dzen_commenter.admin.queries import (
    STATUS_CATEGORIES,
    fetch_feed,
    fetch_status_counts,
    parse_thread_messages,
)
from dzen_commenter.admin.validation import validate_settings_form
from dzen_commenter.config.runtime_config import RuntimeConfig, RuntimeConfigData

templates.env.filters["thread_messages"] = parse_thread_messages


def create_app(
    settings: AdminSettings | None = None, engine: Engine | None = None
) -> FastAPI:
    settings = settings or AdminSettings()
    app = FastAPI(title="Dzen Commenter — админ-панель")
    app.state.settings = settings
    app.state.engine = engine
    app.state.runtime_config = RuntimeConfig(settings.RUNTIME_CONFIG_PATH)

    app.add_middleware(SessionMiddleware, secret_key=settings.ADMIN_SESSION_SECRET)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(auth.router)

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=HTTP_302_FOUND)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request, _: None = Depends(require_login)):
        engine = _get_engine(request.app)
        counts = (
            fetch_status_counts(engine)
            if engine is not None
            else {category: 0 for category in STATUS_CATEGORIES}
        )
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"counts": counts, "total": sum(counts.values())},
        )

    @app.get("/comments")
    def comments(request: Request, _: None = Depends(require_login)):
        engine = _get_engine(request.app)
        status = request.query_params.get("status") or ""
        if status not in STATUS_CATEGORIES:
            status = ""
        author_query = request.query_params.get("q") or ""
        feed = (
            fetch_feed(engine, status=status or None, author_query=author_query or None)
            if engine is not None
            else []
        )
        return templates.TemplateResponse(
            request=request,
            name="comments.html",
            context={"feed": feed, "status": status, "q": author_query},
        )

    @app.get("/settings")
    def settings_page(request: Request, _: None = Depends(require_login)):
        data = request.app.state.runtime_config.get()
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "values": _runtime_values(data),
                "vnc": _vnc_values(request.app.state.settings),
                "errors": {},
                "saved": request.query_params.get("saved") == "1",
            },
        )

    @app.post("/settings")
    async def settings_submit(request: Request, _: None = Depends(require_login)):
        form = await request.form()
        data, errors = validate_settings_form(form)
        if errors:
            return templates.TemplateResponse(
                request=request,
                name="settings.html",
                context={
                    "values": _form_values(form),
                    "vnc": _vnc_values(request.app.state.settings),
                    "errors": errors,
                    "saved": False,
                },
            )

        request.app.state.runtime_config.save(data)
        return RedirectResponse("/settings?saved=1", status_code=HTTP_302_FOUND)

    return app


def _get_engine(app: FastAPI) -> Engine | None:
    engine = app.state.engine
    if engine is None and app.state.settings.DATABASE_URL:
        engine = create_engine(app.state.settings.DATABASE_URL)
        app.state.engine = engine
    return engine


def _runtime_values(data: RuntimeConfigData) -> dict[str, str]:
    return {
        "auto_publish": "on" if data.settings.auto_publish else "",
        "max_comment_age_days": str(data.settings.max_comment_age_days),
        "max_reply_length": str(data.settings.max_reply_length),
        "developer_telegram_chat_ids": data.settings.developer_telegram_chat_ids,
        "error_email_list": data.settings.error_email_list,
        "role": data.prompt.role,
        "tone_of_voice": data.prompt.tone_of_voice,
        "anti_rules": data.prompt.anti_rules,
        "task_lead": data.prompt.task_lead,
        "task_engage": data.prompt.task_engage,
        "cta_marker": data.prompt.cta_marker,
        "cta_link": data.prompt.cta_link,
        "language": data.prompt.language,
    }


def _form_values(form) -> dict[str, str]:
    return {
        name: str(form.get(name, ""))
        for name in (
            "auto_publish",
            "max_comment_age_days",
            "max_reply_length",
            "developer_telegram_chat_ids",
            "error_email_list",
            "role",
            "tone_of_voice",
            "anti_rules",
            "task_lead",
            "task_engage",
            "cta_marker",
            "cta_link",
            "language",
        )
    }


def _vnc_values(settings: AdminSettings) -> dict[str, str]:
    return {
        "host": settings.VNC_HOST,
        "port": str(settings.VNC_PORT),
        "password": settings.VNC_PASSWORD,
    }


app = create_app()
