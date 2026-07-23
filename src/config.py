"""Carga de configuración: config.yaml (no secreto) + variables de entorno (secretos)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    # AnythingLLM (desde env)
    base_url: str
    api_key: str
    # Share montado (desde env)
    root_path: str
    # Desde config.yaml
    poll_interval_seconds: int = 60
    include_extensions: set[str] = field(default_factory=set)
    exclude_globs: list[str] = field(default_factory=list)
    exclude_top_folders: set[str] = field(default_factory=set)
    max_file_mb: int = 50
    dry_run: bool = False
    log_level: str = "INFO"
    state_db_path: str = "/state/sync.sqlite"
    # Timeout HTTP por request (s). Embeber un doc puede tardar mucho; default generoso.
    http_timeout: int = 600

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


def load_config(config_path: str | None = None) -> Config:
    """Lee config.yaml y las env vars requeridas. Lanza si falta algo esencial."""
    cfg_file = Path(config_path or os.getenv("CONFIG_PATH", "/app/config.yaml"))
    data: dict = {}
    if cfg_file.is_file():
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}

    base_url = _require_env("ALLM_BASE_URL").rstrip("/")
    api_key = _require_env("ALLM_API_KEY")
    root_path = os.getenv("ROOT_PATH", "/data/documentos")

    if not Path(root_path).is_dir():
        raise RuntimeError(
            f"ROOT_PATH '{root_path}' no existe o no es un directorio. "
            "¿Está montado el share CIFS?"
        )

    return Config(
        base_url=base_url,
        api_key=api_key,
        root_path=root_path,
        poll_interval_seconds=int(data.get("poll_interval_seconds", 60)),
        include_extensions={e.lower().lstrip(".") for e in data.get("include_extensions", [])},
        exclude_globs=list(data.get("exclude_globs", [])),
        exclude_top_folders=set(data.get("exclude_top_folders", [])),
        max_file_mb=int(data.get("max_file_mb", 50)),
        dry_run=_as_bool(os.getenv("DRY_RUN"), default=bool(data.get("dry_run", False))),
        log_level=str(data.get("log_level", "INFO")).upper(),
        state_db_path=os.getenv("STATE_DB_PATH", "/state/sync.sqlite"),
        http_timeout=int(os.getenv("ALLM_TIMEOUT", str(data.get("http_timeout", 600)))),
    )


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Falta la variable de entorno requerida: {name}")
    return val


def _as_bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}
