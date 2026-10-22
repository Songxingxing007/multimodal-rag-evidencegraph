from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Runtime settings loaded exclusively from backend/.env or environment."""

    app_name: str = "EvidenceGraph Backend"
    app_env: str = "local"
    host: str = "127.0.0.1"
    port: int = 8010
    auth_mode: str = "disabled"
    api_auth_token: str = ""
    data_dir: Path = Path("./data")

    pipeline_mode: str = "local"
    extractor_mode: str = "heuristic"
    qa_mode: str = "auto"

    mineru_token: str = ""
    mineru_base_url: str = "https://mineru.net"
    mineru_model_version: str = "pipeline"
    mineru_language: str = "ch"
    mineru_enable_table: bool = True
    mineru_enable_formula: bool = True
    mineru_is_ocr: bool = False
    mineru_page_range: str = ""
    mineru_poll_interval_sec: float = 5.0
    mineru_poll_timeout_sec: float = 300.0

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_max_output_tokens: int = 8192
    deepseek_thinking: str = "disabled"

    max_upload_mb: int = 200
    max_pages: int = 200
    llm_max_workers: int = 2
    qa_max_tool_calls: int = 6

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir / "backend.sqlite3"

    @property
    def storage_root(self) -> Path:
        return self.resolved_data_dir / "storage"

