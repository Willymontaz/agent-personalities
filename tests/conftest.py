"""Shared fixtures.

The suite runs against the real Ollama backend on purpose: a memory store whose
embeddings are faked in tests proves nothing about recall. If the daemon or the
model is missing the suite stops with instructions rather than degrading.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from memlib import config as configlib  # noqa: E402
from memlib import embedder, store  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def backend_available():
    cfg = configlib.load(REPO)
    try:
        embedder.daemon_version(cfg)
    except embedder.EmbeddingError as exc:
        pytest.exit(f"Ollama is not reachable ({exc}). Start it with `ollama serve`.", returncode=1)
    if not embedder.model_installed(cfg):
        pytest.exit(f"model {cfg.model} is not pulled. Run `bin/memory install`.", returncode=1)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty memory repo: real config, real scopes, no memories yet."""
    root = tmp_path / "repo"
    (root / "memory" / "shared").mkdir(parents=True)
    (root / "memory" / "alice").mkdir()
    (root / "memory" / "bob").mkdir()
    shutil.copy(REPO / "memory" / "config.json", root / "memory" / "config.json")
    shutil.copy(REPO / "memory" / "tags.yaml", root / "memory" / "tags.yaml")
    return root


@pytest.fixture
def cfg(repo: Path) -> configlib.Config:
    return configlib.load(repo)


def days_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).astimezone().replace(
        microsecond=0
    ).isoformat()


def seed(
    cfg: configlib.Config,
    scope: str,
    title: str,
    body: str,
    tags: list[str] | None = None,
    type: str = "fact",
    importance: int = 3,
    created: str | None = None,
    mid: str | None = None,
    supersedes: str | None = None,
) -> store.Memory:
    """Write a memory file straight to disk (no CLI, no reindex)."""
    created = created or store.now_iso()
    mem = store.Memory(
        id=mid or store.new_id(title, created),
        scope=scope,
        title=title,
        body=body,
        type=type,
        tags=tags or [],
        importance=importance,
        author=scope,
        created=created,
        source_session="test",
        supersedes=supersedes,
    )
    store.write_memory(cfg, mem)
    return mem


_cli_module = None


def cli_module():
    """bin/memory loaded as a module (it has no .py extension)."""
    global _cli_module
    if _cli_module is None:
        loader = importlib.machinery.SourceFileLoader("memory_cli", str(REPO / "bin" / "memory"))
        spec = importlib.util.spec_from_loader("memory_cli", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _cli_module = module
    return _cli_module


def run_cli(repo: Path, *argv: str) -> tuple[int, str]:
    """Invoke bin/memory in-process against `repo`, capturing stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_module().main(["--root", str(repo), *argv])
    return code, buf.getvalue()


def load_json(text: str):
    return json.loads(text)
