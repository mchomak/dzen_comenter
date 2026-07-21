from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_302_FOUND

from dzen_commenter.admin import auth
from dzen_commenter.admin.auth import BASE_DIR, NotAuthenticated, require_login, templates
from dzen_commenter.admin.config import AdminSettings


def create_app(settings: AdminSettings | None = None) -> FastAPI:
    settings = settings or AdminSettings()
    app = FastAPI(title="Dzen Commenter — админ-панель")
    app.state.settings = settings

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
        return templates.TemplateResponse(request=request, name="home.html")

    return app


app = create_app()
