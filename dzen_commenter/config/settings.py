from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # БД
    DATABASE_URL: str

    # AI
    AI_PROVIDER: str
    AI_MODEL: str
    AI_API_KEY: str
    AI_BASE_URL: str
    AI_TEMPERATURE: float
    AI_MAX_TOKENS: int
    AI_PROMPT_LANGUAGE: str

    # Браузер / Дзен
    USER_DATA_DIR: str
    STORAGE_STATE_PATH: str
    HEADLESS: bool
    COMMENTS_URL: str

    # Цикл
    POLL_INTERVAL: int
    KEEPALIVE_INTERVAL: int
    AUTO_PUBLISH: bool = False
    MAX_REPLIES_PER_CYCLE: int

    # Telegram (авторизация/уведомления)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_PROXY_URL: str = ""

    # Email-фоллбэк
    EMAIL_FALLBACK_LIST: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    # Отсечки
    MAX_COMMENT_AGE_DAYS: int = 30
    MAX_REPLY_LENGTH: int = 1000

    # Промпт
    PROMPT_CONFIG_PATH: str = ""

    # VNC
    VNC_PORT: int = 5900
    VNC_PASSWORD: str = ""
