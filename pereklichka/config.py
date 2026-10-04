from pathlib import Path
from uuid import UUID

from pydantic import AliasChoices, AwareDatetime, Field, SecretStr, model_validator
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

    moderation_family_id: UUID | None = None
    moderation_recipient_chat_id: int | None = Field(default=None, lt=0)
    moderation_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def paired_moderation_settings(self) -> "Settings":
        values = (
            self.moderation_family_id,
            self.moderation_recipient_chat_id,
            self.moderation_until,
        )
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError("Moderation settings must be supplied together")
        return self
