from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminSettings(BaseSettings):
    """Настройки веб-панели. Читаются из того же .env, что и бот.

    Все поля имеют значения по умолчанию, поэтому импорт приложения не требует
    заполненного окружения (боевые значения задаются через .env/переменные среды).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ADMIN_PASSWORD: str = ""
    ADMIN_SESSION_SECRET: str = "change-me-in-production"
    RUNTIME_CONFIG_PATH: str = "runtime_config.json"
    DATABASE_URL: str = ""
    VNC_HOST: str = "localhost"
    VNC_PORT: int = 5900
    VNC_PASSWORD: str = ""
