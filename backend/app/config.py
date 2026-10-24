"""集中管理配置；环境文件以项目根目录为基准，避免启动目录改变数据位置。"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql://rag:rag_local@127.0.0.1:5432/rag")
    data_dir: Path = ROOT / "data"
    model_cache_dir: Path = ROOT / "data/models"
    embedding_model: str = "google/embeddinggemma-300m"
    embedding_revision: str = ""
    embedding_device: str = "cpu"
    embedding_batch_size: int = Field(4, ge=1, le=32)
    chunk_chars: int = Field(700, ge=100, le=2000)
    chunk_overlap: int = Field(80, ge=0, le=500)
    # 0.5 只是首次调试起点，不代表已经达到中文面经验收质量。
    similarity_threshold: float = Field(0.5, ge=-1, le=1)
    top_k: int = Field(8, ge=1, le=50)
    max_upload_mb: int = Field(10, ge=1, le=100)
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout_seconds: float = Field(30, gt=0, le=120)
    llm_max_tokens: int = Field(3000, ge=256, le=16000)
    context_token_budget: int = Field(8000, ge=512, le=32000)
    # 完整问答含证据原文；默认仅保留 30 天，维护线程定期清理终态记录。
    audit_retention_days: int = Field(30, ge=1, le=3650)
    audit_cleanup_interval_seconds: int = Field(3600, ge=1, le=86400)
    worker_enabled: bool = True
    frontend_origin: str = "http://127.0.0.1:5173"

    @field_validator("data_dir", "model_cache_dir")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_chars:
            raise ValueError("CHUNK_OVERLAP 必须小于 CHUNK_CHARS")
        return self
