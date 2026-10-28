"""Memory files on disk (source of truth) and the SQLite index derived from them.

A memory is one markdown file:

    ---
    id: 2026-09-28-prefers-terse-reviews
    scope: shared
    ...
    ---
    One-line title
    Body, 2-8 sentences, self-contained.

The index in memory/.index.sqlite is throwaway except for the `usage` table,
which records what recall actually surfaced and survives `reindex --force`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import Config
from . import embedder

TYPES = ("fact", "preference", "decision", "event", "relationship", "insight")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id             TEXT PRIMARY KEY,
    scope          TEXT NOT NULL,
    path           TEXT NOT NULL,
    sha            TEXT NOT NULL,
    author         TEXT,
    type           TEXT,
    importance     INTEGER,
    created        TEXT,
    source_session TEXT,
    tags_json      TEXT,
    title          TEXT,
    body           TEXT,
    supersedes     TEXT,
    superseded_by  TEXT,
    reviewed       TEXT,
    model          TEXT,
    recipe         TEXT,
    vec            BLOB
);
CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope);

CREATE TABLE IF NOT EXISTS usage (
    id                TEXT PRIMARY KEY,
    recalled_count    INTEGER NOT NULL DEFAULT 0,
    last_recalled     TEXT,
    injected_sessions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class StoreError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# tiny YAML subset: flat key/value plus flow or block lists of scalars
# --------------------------------------------------------------------------

def _parse_scalar(raw: str):
    raw = raw.strip()
    if raw in ("", "null", "~", "None"):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw in ("true", "false"):
        return raw == "true"
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---`-delimited frontmatter from the rest. Returns (fields, rest)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text

    fields: dict = {}
    pending_list_key: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.lstrip().startswith("- ") and pending_list_key:
            fields[pending_list_key].append(_parse_scalar(line.lstrip()[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        pending_list_key = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fields[key] = [_parse_scalar(p) for p in inner.split(",") if p.strip()] if inner else []
        elif value == "":
            fields[key] = []
            pending_list_key = key
        else:
            fields[key] = _parse_scalar(value)
    return fields, "\n".join(lines[end + 1 :]).lstrip("\n")


def _emit_scalar(value) -> str:
    if value is None:
        return "null"
    text = str(value)
    if text == "" or re.search(r"[:#\[\]{}]|^\s|\s$", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


# --------------------------------------------------------------------------
# memory records
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_days(value: str | None, ref: datetime | None = None) -> float:
    dt = parse_dt(value)
    if dt is None:
        return 0.0
    ref = ref or datetime.now(timezone.utc)
    return max(0.0, (ref - dt).total_seconds() / 86400.0)


def slugify(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "memory"


def split_ids(value) -> list[str]:
    """Normalise a `supersedes` value to a list of ids.

    Accepts None, a comma-separated string, or a YAML list (a hand-edited file
    may carry `supersedes: [a, b]`). Never raises on shape.
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value]
    else:
        parts = [p.strip() for p in str(value).split(",")]
    return [p for p in parts if p]


@dataclass
class Memory:
    id: str
    scope: str
    title: str
    body: str
    type: str = "fact"
    tags: list[str] = field(default_factory=list)
    importance: int = 3
    author: str = ""
    created: str = ""
    source_session: str = ""
    supersedes: str | None = None
    reviewed: str | None = None
    path: Path | None = None
    sha: str = ""

    def embed_text(self) -> str:
        return embedder.passage_text(self.title, self.tags, self.body)

    def render(self) -> str:
        head = [
            "---",
            f"id: {_emit_scalar(self.id)}",
            f"scope: {_emit_scalar(self.scope)}",
            f"author: {_emit_scalar(self.author)}",
            f"type: {_emit_scalar(self.type)}",
            "tags: [" + ", ".join(self.tags) + "]",
            f"importance: {int(self.importance)}",
            f"created: {_emit_scalar(self.created)}",
            f"source_session: {_emit_scalar(self.source_session)}",
            f"supersedes: {_emit_scalar(self.supersedes)}",
        ]
        if self.reviewed:
            head.append(f"reviewed: {_emit_scalar(self.reviewed)}")
        head.append("---")
        return "\n".join(head) + "\n" + self.title.strip() + "\n" + self.body.strip() + "\n"


def read_memory(path: Path, scope: str | None = None) -> Memory:
    text = path.read_text(encoding="utf-8")
    fields, rest = parse_frontmatter(text)
    lines = rest.strip().splitlines()
    title = lines[0].strip() if lines else ""
    body = "\n".join(lines[1:]).strip()
    tags = fields.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    raw_importance = fields.get("importance")
    try:
        importance = int(raw_importance) if raw_importance is not None else 3
    except (TypeError, ValueError):
        raise StoreError(f"{path}: importance must be an integer, got {raw_importance!r}")

    supersedes_ids = split_ids(fields.get("supersedes"))
    return Memory(
        id=str(fields.get("id") or path.stem),
        scope=str(fields.get("scope") or scope or path.parent.name),
        title=title,
        body=body,
        type=str(fields.get("type") or "fact"),
        tags=[str(t) for t in tags],
        importance=importance,
        author=str(fields.get("author") or ""),
        created=str(fields.get("created") or ""),
        source_session=str(fields.get("source_session") or ""),
        supersedes=", ".join(supersedes_ids) or None,
        reviewed=fields.get("reviewed") or None,
        path=path,
        sha=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def scan(cfg: Config) -> tuple[list[Memory], list[str]]:
    """Every live memory file (memory/<scope>/*.md, never _archive).

    Returns (memories, errors). A file that cannot be parsed is skipped and its
    reason recorded in `errors` rather than aborting the whole scan — one bad
    hand-edit must never take the index (and therefore recall) down with it.
    Duplicate ids across scopes are caught here too: the first wins, later ones
    are reported.
    """
    out: list[Memory] = []
    errors: list[str] = []
    seen: dict[str, Path] = {}
    for scope in cfg.scopes():
        for path in sorted(cfg.scope_dir(scope).glob("*.md")):
            try:
                mem = read_memory(path, scope)
            except StoreError as exc:
                errors.append(str(exc))
                continue
            if mem.id in seen:
                errors.append(
                    f"{path.relative_to(cfg.root)}: duplicate id {mem.id!r} "
                    f"(already used by {seen[mem.id].relative_to(cfg.root)}); skipped"
                )
                continue
            seen[mem.id] = path
            out.append(mem)
    return out, errors


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------

def connect(cfg: Config) -> sqlite3.Connection:
    cfg.memory_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.index_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def index_is_stale(cfg: Config) -> bool:
    """Cheap check used by bin/persona: does the index need a reindex run?"""
    if not cfg.index_path.exists():
        return True
    try:
        conn = connect(cfg)
    except sqlite3.Error:
        return True
    try:
        if _meta_get(conn, "model") != cfg.model or _meta_get(conn, "recipe") != cfg.recipe:
            return True
        if _meta_get(conn, "dims") != str(cfg.dims):
            return True
        indexed = {r["id"]: r["sha"] for r in conn.execute("SELECT id, sha FROM memories")}
    finally:
        conn.close()

    on_disk = {}
    for scope in cfg.scopes():
        for path in cfg.scope_dir(scope).glob("*.md"):
            on_disk[path.stem] = path
    if set(indexed) != set(on_disk):
        return True

    index_mtime = cfg.index_path.stat().st_mtime
    return any(p.stat().st_mtime > index_mtime for p in on_disk.values())


def reindex(cfg: Config, force: bool = False, conn: sqlite3.Connection | None = None) -> dict:
    """Rebuild `memories` from disk. Re-embeds only what changed.

    Cache key is (file sha, model, recipe) - the index invalidates itself
    exactly when one of the three changes. `usage` is never touched.
    """
    owned = conn is None
    conn = conn or connect(cfg)
    try:
        cached: dict[str, sqlite3.Row] = {
            r["id"]: r for r in conn.execute("SELECT * FROM memories")
        }
        memories, errors = scan(cfg)

        reusable: dict[str, bytes] = {}
        to_embed: list[Memory] = []
        for mem in memories:
            row = cached.get(mem.id)
            fresh = (
                not force
                and row is not None
                and row["sha"] == mem.sha
                and row["model"] == cfg.model
                and row["recipe"] == cfg.recipe
                and row["vec"] is not None
                and len(row["vec"]) == cfg.dims * 4
            )
            if fresh:
                reusable[mem.id] = row["vec"]
            else:
                to_embed.append(mem)

        if to_embed:
            vecs = embedder.embed(cfg, [m.embed_text() for m in to_embed], kind="passage")
            for mem, vec in zip(to_embed, vecs):
                reusable[mem.id] = np.ascontiguousarray(vec, dtype=np.float32).tobytes()

        conn.execute("DELETE FROM memories")
        for mem in memories:
            conn.execute(
                """INSERT INTO memories
                   (id, scope, path, sha, author, type, importance, created, source_session,
                    tags_json, title, body, supersedes, superseded_by, reviewed, model, recipe, vec)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)""",
                (
                    mem.id,
                    mem.scope,
                    str(mem.path.relative_to(cfg.root)) if mem.path else "",
                    mem.sha,
                    mem.author,
                    mem.type,
                    mem.importance,
                    mem.created,
                    mem.source_session,
                    "\n".join(mem.tags),
                    mem.title,
                    mem.body,
                    mem.supersedes,
                    mem.reviewed,
                    cfg.model,
                    cfg.recipe,
                    reusable[mem.id],
                ),
            )

        # superseded_by is derived: A supersedes B  =>  B.superseded_by = A
        for mem in memories:
            for old in split_ids(mem.supersedes):
                conn.execute(
                    "UPDATE memories SET superseded_by = ? WHERE id = ?", (mem.id, old)
                )

        _meta_set(conn, "model", cfg.model)
        _meta_set(conn, "recipe", cfg.recipe)
        _meta_set(conn, "dims", str(cfg.dims))
        _meta_set(conn, "reindexed", now_iso())
        _meta_set(conn, "errors", json.dumps(errors))
        conn.commit()
        return {
            "total": len(memories),
            "embedded": len(to_embed),
            "reused": len(reusable) - len(to_embed),
            "errors": errors,
        }
    finally:
        if owned:
            conn.close()


def ensure_fresh(cfg: Config) -> None:
    if index_is_stale(cfg):
        reindex(cfg)


def index_errors(cfg: Config, conn: sqlite3.Connection | None = None) -> list[str]:
    """The files the last reindex could not parse (empty when all is well)."""
    owned = conn is None
    conn = conn or connect(cfg)
    try:
        raw = _meta_get(conn, "errors")
    finally:
        if owned:
            conn.close()
    if not raw:
        return []
    try:
        return list(json.loads(raw))
    except (ValueError, TypeError):
        return []


def tags_of(row: sqlite3.Row) -> list[str]:
    return [t for t in (row["tags_json"] or "").split("\n") if t]


def load_scope(
    conn: sqlite3.Connection, cfg: Config, scopes: list[str] | None, include_superseded: bool = False
) -> tuple[list[sqlite3.Row], np.ndarray]:
    sql = "SELECT * FROM memories"
    params: list = []
    clauses = []
    if scopes:
        clauses.append("scope IN (%s)" % ",".join("?" * len(scopes)))
        params.extend(scopes)
    if not include_superseded:
        clauses.append("superseded_by IS NULL")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = list(conn.execute(sql, params))
    if not rows:
        return [], np.zeros((0, cfg.dims), dtype=np.float32)
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    return rows, mat


def record_recall(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Record that these memories were injected once.

    Recall dedupes per session, so a memory is injected at most once per
    session: `injected_sessions` therefore tracks `recalled_count` exactly, and
    both increment by one here.
    """
    stamp = now_iso()
    for mid in ids:
        conn.execute(
            """INSERT INTO usage(id, recalled_count, last_recalled, injected_sessions)
               VALUES(?, 1, ?, 1)
               ON CONFLICT(id) DO UPDATE SET
                 recalled_count = recalled_count + 1,
                 last_recalled = excluded.last_recalled,
                 injected_sessions = injected_sessions + 1""",
            (mid, stamp),
        )
    conn.commit()


def usage_of(conn: sqlite3.Connection, mid: str) -> dict:
    row = conn.execute("SELECT * FROM usage WHERE id = ?", (mid,)).fetchone()
    if not row:
        return {"recalled_count": 0, "last_recalled": None, "injected_sessions": 0}
    return dict(row)


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def new_id(title: str, created: str | None = None) -> str:
    day = (parse_dt(created) or datetime.now(timezone.utc)).astimezone().strftime("%Y-%m-%d")
    return f"{day}-{slugify(title)}"


def id_taken(cfg: Config, mem_id: str, keep: Path | None = None) -> bool:
    """Is `<mem_id>.md` present in any live scope (ids are unique across scopes)?"""
    for scope in cfg.scopes():
        candidate = cfg.scope_dir(scope) / f"{mem_id}.md"
        if candidate.exists() and candidate != keep:
            return True
    return False


def write_memory(cfg: Config, mem: Memory) -> Path:
    scope_dir = cfg.scope_dir(mem.scope)
    scope_dir.mkdir(parents=True, exist_ok=True)
    # Ids must be unique across every scope, not just this one: the SQLite index
    # keys on id alone, so a shared and a persona memory with the same day+slug
    # would collide and take reindex (and recall) down.
    n = 2
    while id_taken(cfg, mem.id, keep=mem.path):
        mem.id = f"{new_id(mem.title, mem.created)}-{n}"
        n += 1
    path = scope_dir / f"{mem.id}.md"
    path.write_text(mem.render(), encoding="utf-8")
    mem.path = path
    return path


def archive(cfg: Config, mid: str, conn: sqlite3.Connection | None = None) -> Path:
    """Move a memory out of the live set. Nothing is ever deleted."""
    owned = conn is None
    conn = conn or connect(cfg)
    try:
        row = conn.execute("SELECT scope, path FROM memories WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise StoreError(f"unknown memory id: {mid}")
        src = cfg.root / row["path"]
        dest_dir = cfg.archive_dir / row["scope"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 2
        while dest.exists():
            dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
            n += 1
        src.rename(dest)
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        conn.commit()
        return dest
    finally:
        if owned:
            conn.close()


def set_field(path: Path, key: str, value) -> None:
    """Rewrite one frontmatter field in place, preserving everything else."""
    mem = read_memory(path)
    setattr(mem, key, value)
    path.write_text(mem.render(), encoding="utf-8")
