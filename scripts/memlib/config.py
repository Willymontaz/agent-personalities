"""Loads memory/config.json and resolves every path the memory store uses.

The repo root is either PERSONA_REPO_ROOT (tests point this at a tmp dir) or the
directory two levels above this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "backend": "ollama",
    "model": "bge-m3",
    "dims": 1024,
    "host": "http://127.0.0.1:11434",
    "keep_alive": "30m",
    "timeout": 120,
    "batch_size": 16,
    "recipe": "v1",
    "query_prefix": "",
    "passage_prefix": "",
    "tags_path": "memory/tags.yaml",
    "retrieval": {
        "k": 3,
        "threshold": 0.50,
        "tag_boost_per_match": 0.05,
        "tag_boost_cap": 0.10,
        "recency_base": 0.85,
        "recency_span": 0.15,
        "recency_scale_days": 180,
        "importance_weight": 0.02,
    },
    "compaction": {
        "duplicate_threshold": 0.88,
        "near_duplicate_threshold": 0.90,
        "stale_max_importance": 2,
        "stale_age_days": 180,
        "stale_last_recalled_days": 120,
        "oversized_chars": 1200,
        "reviewed_grace_days": 90,
        "nudge_threshold": 10,
    },
}


class ConfigError(RuntimeError):
    pass


def repo_root() -> Path:
    env = os.environ.get("PERSONA_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict, root: Path):
        self.root = root
        self.data = data

    # --- embedding backend -------------------------------------------------
    @property
    def backend(self) -> str:
        return self.data["backend"]

    @property
    def model(self) -> str:
        return self.data["model"]

    @property
    def dims(self) -> int:
        return int(self.data["dims"])

    @property
    def host(self) -> str:
        return self.data["host"].rstrip("/")

    @property
    def keep_alive(self) -> str:
        return self.data["keep_alive"]

    @property
    def timeout(self) -> float:
        return float(self.data["timeout"])

    @property
    def batch_size(self) -> int:
        return int(self.data["batch_size"])

    @property
    def recipe(self) -> str:
        return str(self.data["recipe"])

    @property
    def query_prefix(self) -> str:
        return self.data.get("query_prefix", "")

    @property
    def passage_prefix(self) -> str:
        return self.data.get("passage_prefix", "")

    # --- tuning blocks -----------------------------------------------------
    @property
    def retrieval(self) -> dict:
        return self.data["retrieval"]

    @property
    def compaction(self) -> dict:
        return self.data["compaction"]

    # --- paths -------------------------------------------------------------
    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def archive_dir(self) -> Path:
        return self.memory_dir / "_archive"

    @property
    def index_path(self) -> Path:
        return self.memory_dir / ".index.sqlite"

    @property
    def tags_path(self) -> Path:
        return self.root / self.data["tags_path"]

    @property
    def personas_dir(self) -> Path:
        return self.root / "personas"

    @property
    def agents_dir(self) -> Path:
        return self.root / ".claude" / "agents"

    def scope_dir(self, scope: str) -> Path:
        return self.memory_dir / scope

    def scopes(self) -> list[str]:
        """Every scope directory that exists on disk, shared first."""
        if not self.memory_dir.is_dir():
            return []
        names = sorted(
            p.name
            for p in self.memory_dir.iterdir()
            if p.is_dir() and not p.name.startswith((".", "_"))
        )
        if "shared" in names:
            names.remove("shared")
            names.insert(0, "shared")
        return names


def load(root: Path | str | None = None) -> Config:
    root = Path(root).expanduser().resolve() if root else repo_root()
    path = root / "memory" / "config.json"
    if not path.is_file():
        raise ConfigError(f"missing config: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    return Config(_merge(DEFAULTS, raw), root)
