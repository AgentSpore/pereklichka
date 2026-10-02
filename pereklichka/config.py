from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str


class Settings(DatabaseSettings):
    skill_id: str
    bot_token: SecretStr | None = None
    bot_proxy: SecretStr | None = None
    bot_proxy_file: Path | None = None
    bot_token_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BOT_TOKEN_FILE", "TELEGRAM_BOT_TOKEN_FILE", "bot_token_file"
        ),
    )
