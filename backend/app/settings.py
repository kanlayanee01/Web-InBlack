from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    instance_id: str = "local"
    database_url: str
    redis_url: str
    order_ttl_seconds: int = 900
    payment_webhook_secret: str
    seed_admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
