from datetime import date, datetime, time, timedelta

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
    parse_thread_messages,
    unique_authors,
)
from dzen_commenter.admin.validation import split_csv_items, validate_settings_form
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
        status, author_query = _feed_filters(request)
        feed = (
            fetch_feed(engine, limit=100, status=status or None, author_query=author_query or None)
            if engine is not None
            else []
        )
        return templates.TemplateResponse(
            request=request,
            name="comments.html",
            context={
                "feed": feed,
                "authors": unique_authors(feed),
                "status": status,
                "q": author_query,
                "is_history": False,
                "route": "/",
                "date_from": "",
                "date_to": "",
                "order": "desc",
            },
        )

    @app.get("/comments")
    def comments(request: Request, _: None = Depends(require_login)):
        engine = _get_engine(request.app)
        status, author_query = _feed_filters(request)
        raw_date_from = request.query_params.get("date_from") or ""
        raw_date_to = request.query_params.get("date_to") or ""
        parsed_date_from = _parse_date(raw_date_from)
        parsed_date_to = _parse_date(raw_date_to)
        order = request.query_params.get("order")
        order = order if order in {"asc", "desc"} else "desc"
        date_from = datetime.combine(parsed_date_from, time.min) if parsed_date_from else None
        date_to = (
            datetime.combine(parsed_date_to + timedelta(days=1), time.min)
            if parsed_date_to
            else None
        )
        feed = (
            fetch_feed(
                engine,
                limit=None,
                status=status or None,
                author_query=author_query or None,
                date_from=date_from,
                date_to=date_to,
                order=order,
            )
            if engine is not None
            else []
        )
        return templates.TemplateResponse(
            request=request,
            name="comments.html",
            context={
                "feed": feed,
                "authors": unique_authors(feed),
                "status": status,
                "q": author_query,
                "is_history": True,
                "route": "/comments",
                "date_from": parsed_date_from.isoformat() if parsed_date_from else "",
                "date_to": parsed_date_to.isoformat() if parsed_date_to else "",
                "order": order,
            },
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


def _feed_filters(request: Request) -> tuple[str, str]:
    status = request.query_params.get("status") or ""
    if status not in STATUS_CATEGORIES:
        status = ""
    return status, request.query_params.get("q") or ""


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _runtime_values(data: RuntimeConfigData) -> dict[str, object]:
    return {
        "auto_publish": "on" if data.settings.auto_publish else "",
        "max_comment_age_days": str(data.settings.max_comment_age_days),
        "max_reply_length": str(data.settings.max_reply_length),
        "developer_telegram_chat_ids": split_csv_items(data.settings.developer_telegram_chat_ids),
        "error_email_list": split_csv_items(data.settings.error_email_list),
        "role": data.prompt.role,
        "tone_of_voice": data.prompt.tone_of_voice,
        "anti_rules": data.prompt.anti_rules,
        "task_lead": data.prompt.task_lead,
        "task_engage": data.prompt.task_engage,
        "cta_marker": data.prompt.cta_marker,
        "cta_link": data.prompt.cta_link,
        "language": data.prompt.language,
    }


def _form_values(form) -> dict[str, object]:
    values: dict[str, object] = {
        name: str(form.get(name, ""))
        for name in (
            "auto_publish",
            "max_comment_age_days",
            "max_reply_length",
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
    for name in ("developer_telegram_chat_ids", "error_email_list"):
        values[name] = [item.strip() for item in form.getlist(name) if item.strip()]
    return values


def _vnc_values(settings: AdminSettings) -> dict[str, str]:
    return {
        "host": settings.VNC_HOST,
        "port": str(settings.VNC_PORT),
        "password": settings.VNC_PASSWORD,
    }


app = create_app()
