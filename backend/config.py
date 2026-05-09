"""
config.py
Single source of truth for all application settings.
Loaded from .env via pydantic-settings — zero hardcoded values anywhere else.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # ERPNext
    erpnext_base_url: str = Field(..., description="Base URL of your ERPNext instance")
    erpnext_api_key: str = Field(..., description="ERPNext API key")
    erpnext_api_secret: str = Field(..., description="ERPNext API secret")

    # Groq LLM
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field(default="llama3-70b-8192", description="Groq model to use")

    # Anomaly Detection
    anomaly_threshold_pct: float = Field(
        default=15.0,
        description="Percentage change in net pay that triggers an anomaly flag"
    )

    # App
    app_version: str = Field(default="1.0.0")
    app_env: str = Field(default="development")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Call get_settings() everywhere — never instantiate Settings() directly.
    """
    return Settings()


# Convenience alias — import this in other modules
settings = get_settings()