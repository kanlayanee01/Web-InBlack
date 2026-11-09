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
    mock_pay_public_url: str = "http://localhost:8090"
    mock_pay_url: str = "http://mock-pay:8000"
    mock_game_url: str = "http://mock-game:8000"
    game_timeout_seconds: int = 5
    worker_concurrency: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
