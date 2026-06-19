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
