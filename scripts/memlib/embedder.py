"""Ollama embedding client (stdlib urllib + numpy, nothing else).

Every vector leaves this module L2-normalised, so a dot product is a cosine.
Failures raise; nothing here ever falls back to a weaker embedding silently.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import numpy as np

from .config import Config


class EmbeddingError(RuntimeError):
    pass


def _post(cfg: Config, path: str, payload: dict, timeout: float | None = None) -> dict:
    req = urllib.request.Request(
        cfg.host + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or cfg.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise EmbeddingError(f"ollama {path} HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EmbeddingError(
            f"ollama unreachable at {cfg.host} ({exc}). Start it with `ollama serve`."
        ) from exc


def _get(cfg: Config, path: str, timeout: float = 5.0) -> dict:
    try:
        with urllib.request.urlopen(cfg.host + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any failure means "daemon not usable"
        raise EmbeddingError(f"ollama unreachable at {cfg.host} ({exc})") from exc


def daemon_version(cfg: Config) -> str:
    return _get(cfg, "/api/version").get("version", "unknown")


def installed_models(cfg: Config) -> list[str]:
    data = _get(cfg, "/api/tags", timeout=10.0)
    return [m["name"] for m in data.get("models", [])]


def model_installed(cfg: Config, model: str | None = None) -> bool:
    model = model or cfg.model
    names = installed_models(cfg)
    base = {n.split(":", 1)[0] for n in names}
    return model in names or model in base or f"{model}:latest" in names


def passage_text(title: str, tags: list[str], body: str) -> str:
    """The exact string that gets embedded for a memory.

    Tags sit inside the embedded text on purpose: that is what makes a query
    about "code review" land on a memory tagged topics/code-review even when the
    body never uses the words.
    """
    return f"{title}\ntags: {', '.join(tags)}\n{body}".strip()


def _normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def embed(cfg: Config, texts: list[str], kind: str = "passage") -> np.ndarray:
    """Embed a list of texts. Returns float32 (n, dims), L2-normalised."""
    if cfg.backend != "ollama":
        raise EmbeddingError(f"unsupported backend: {cfg.backend!r}")
    if not texts:
        return np.zeros((0, cfg.dims), dtype=np.float32)

    prefix = cfg.query_prefix if kind == "query" else cfg.passage_prefix
    prepared = [prefix + t for t in texts]

    vectors: list[list[float]] = []
    for start in range(0, len(prepared), cfg.batch_size):
        batch = prepared[start : start + cfg.batch_size]
        data = _post(
            cfg,
            "/api/embed",
            {"model": cfg.model, "input": batch, "keep_alive": cfg.keep_alive},
        )
        got = data.get("embeddings")
        if not got or len(got) != len(batch):
            raise EmbeddingError(
                f"ollama returned {len(got or [])} embeddings for {len(batch)} inputs"
            )
        vectors.extend(got)

    mat = np.asarray(vectors, dtype=np.float32)
    if mat.shape[1] != cfg.dims:
        raise EmbeddingError(
            f"model {cfg.model} returned {mat.shape[1]} dims but config says {cfg.dims}. "
            "Fix memory/config.json and run `bin/memory reindex --force`."
        )
    return _normalise(mat)


def embed_query(cfg: Config, text: str) -> np.ndarray:
    return embed(cfg, [text], kind="query")[0]

