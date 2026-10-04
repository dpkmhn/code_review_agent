from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import Literal
from pydantic import model_validator
from urllib.parse import urlsplit


class Settings(BaseSettings):
    github_token: str
    openai_api_key: str
    # Reasoning for the tool-calling reviewer step. "none" matches the original
    # Chat Completions behavior; compare levels with the evaluation harness.
    verification_reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    sandbox_enabled: bool = False
    sandbox_image: str = "code-review-sandbox:stage9"
    database_url: SecretStr | None = None
    environment: Literal["development", "production"] = "development"
    api_access_token: SecretStr | None = None
    public_origin: str | None = None
    sandbox_worker_url: str | None = None
    sandbox_worker_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.environment == "production":
            if not self.api_access_token or len(self.api_access_token.get_secret_value()) < 32:
                raise ValueError("Production requires an api_access_token of at least 32 characters.")
            origin = urlsplit(self.public_origin or "")
            if (origin.scheme != "https" or not origin.hostname or origin.username
                    or origin.password or origin.path or origin.query or origin.fragment):
                raise ValueError("Production requires public_origin as an HTTPS origin without a trailing slash.")
            if self.database_url is None:
                raise ValueError("Production requires database_url.")
            if self.sandbox_enabled and not self.sandbox_worker_url:
                raise ValueError("Production sandbox execution requires a separate worker.")
        if self.sandbox_worker_url and (not self.sandbox_worker_token or len(self.sandbox_worker_token.get_secret_value()) < 32):
            raise ValueError("A sandbox worker requires a token of at least 32 characters.")
        if self.sandbox_worker_url:
            worker = urlsplit(self.sandbox_worker_url)
            if (worker.scheme not in ("http", "https") or not worker.hostname or worker.username
                    or worker.password or worker.query or worker.fragment):
                raise ValueError("Use an HTTP(S) sandbox worker URL without credentials, query, or fragment.")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
