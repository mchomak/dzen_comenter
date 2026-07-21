import hmac
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_302_FOUND, HTTP_401_UNAUTHORIZED

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


class NotAuthenticated(Exception):
    """Поднимается зависимостью require_login для гостя; обработчик редиректит на /login."""


def require_login(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise NotAuthenticated()


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.post("/login")
def login_submit(request: Request, password: str = Form("")):
    configured = request.app.state.settings.ADMIN_PASSWORD
    if configured and hmac.compare_digest(password, configured):
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Неверный пароль"},
        status_code=HTTP_401_UNAUTHORIZED,
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=HTTP_302_FOUND)
