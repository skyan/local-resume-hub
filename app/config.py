from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    resume_root: Path
    db_path: Path
    scan_interval_seconds: int = 300
    enable_llm_enhance: bool = True
    llm_provider: str = "aliyun_bailian"
    llm_model: str = "qwen-doc-turbo"
    dashscope_api_key: str = ""


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}


def _expand(path_value: str, default: str) -> Path:
    raw = os.getenv(path_value, default)
    return Path(raw).expanduser().resolve()


def load_settings() -> Settings:
    resume_root = _expand("RESUME_ROOT", "~/Documents/候选人")
    db_path = _expand("DB_PATH", "./data/resumes.db")
    scan_interval_seconds = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
    enable_llm_enhance = os.getenv("ENABLE_LLM_ENHANCE", "true").lower() in {"1", "true", "yes", "on"}

    settings = Settings(
        resume_root=resume_root,
        db_path=db_path,
        scan_interval_seconds=scan_interval_seconds,
        enable_llm_enhance=enable_llm_enhance,
        llm_provider=os.getenv("LLM_PROVIDER", "aliyun_bailian"),
        llm_model=os.getenv("LLM_MODEL", "qwen-doc-turbo"),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
    )
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
