import pytest
from fastapi.testclient import TestClient

from dzen_commenter.admin.app import create_app
from dzen_commenter.admin.config import AdminSettings

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client() -> TestClient:
    settings = AdminSettings(
        _env_file=None,
        ADMIN_PASSWORD=PASSWORD,
        ADMIN_SESSION_SECRET="test-session-secret",
    )
    app = create_app(settings)
    # follow_redirects=False, чтобы проверять сами 302-редиректы.
    return TestClient(app, follow_redirects=False)


def test_guest_protected_page_redirects_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_login_with_correct_password_grants_access(client):
    resp = client.post("/login", data={"password": PASSWORD})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    # Сессия установлена — защищённая страница теперь доступна.
    home = client.get("/")
    assert home.status_code == 200


def test_login_with_wrong_password_is_rejected(client):
    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Неверный пароль" in resp.text

    # Доступ не выдан.
    home = client.get("/")
    assert home.status_code == 302
    assert home.headers["location"] == "/login"


def test_logout_clears_session(client):
    client.post("/login", data={"password": PASSWORD})
    assert client.get("/").status_code == 200

    resp = client.post("/logout")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"

    # После logout защищённая страница снова редиректит на /login.
    home = client.get("/")
    assert home.status_code == 302
    assert home.headers["location"] == "/login"


def test_health_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_sidebar_present_in_base_template():
    from dzen_commenter.admin import auth

    base = (auth.BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "Комментарии" in base
    assert "Настройки" in base
    assert 'class="sidebar"' in base
