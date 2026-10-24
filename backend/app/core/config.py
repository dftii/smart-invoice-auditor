"""Application settings loaded from environment variables and an optional .env file."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "SmartInvoice-FACe Auditor"
    app_version: str = "1.0.0"
    environment: str = Field(default="development", description="development | production | test")
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])
    log_level: str = "INFO"

    # Database. SQLite by default; set e.g. mysql+pymysql://user:pass@host:3306/smartinvoice for MySQL.
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'smartinvoice.db').as_posix()}"
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # File storage
    upload_dir: Path = BACKEND_DIR / "data" / "uploads"
    max_upload_size_mb: int = 15
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: ["application/pdf", "image/png", "image/jpeg", "image/webp"]
    )

    # AI extraction. Without ANTHROPIC_API_KEY the extractor runs in demo mode.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    ai_timeout_seconds: float = 120.0
    ai_fallback_to_demo: bool = True

    # Audit
    audit_tolerance: Decimal = Decimal("0.01")
    allowed_vat_rates: list[Decimal] = Field(
        default_factory=lambda: [Decimal(21), Decimal(10), Decimal(4), Decimal(0)]
    )
    allowed_irpf_rates: list[Decimal] = Field(
        default_factory=lambda: [Decimal(0), Decimal(1), Decimal(2), Decimal(7), Decimal(15), Decimal(19)]
    )

    # Facturae / FACe
    facturae_cert_path: Path | None = None
    facturae_cert_password: str | None = None

    @field_validator("anthropic_api_key", "facturae_cert_password", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("facturae_cert_path", mode="before")
    @classmethod
    def empty_path_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
