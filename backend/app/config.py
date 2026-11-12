from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"),
    )
    supabase_publishable_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"),
    )
    x_username: str = ""
    x_password: str = ""
    x_handle: str = ""
    chatgpt_email: str = ""
    chatgpt_password: str = ""
    browser_profile_dir: str = str(BACKEND_ROOT / "data" / "chrome-profile")
    x_home_url: str = "https://x.com/home"
    chatgpt_home_url: str = "https://chatgpt.com/"
    grok_home_url: str = "https://x.com/i/grok"
    headless: bool = False
    database_mode: str = "auto"
    local_store_path: str = ""
    host: str = "127.0.0.1"
    port: int = 8787

    @property
    def profile_path(self) -> Path:
        path = Path(self.browser_profile_dir)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path

    @property
    def store_path(self) -> Path:
        if self.local_store_path:
            path = Path(self.local_store_path)
            if not path.is_absolute():
                path = BACKEND_ROOT / path
            return path
        return BACKEND_ROOT / "data" / "store.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
