"""Compaction: find what has gone redundant or dead, then apply a reviewed plan.

`report()` only reads. `apply()` only performs actions the user already agreed
to, and never deletes: merged and archived files move to memory/_archive/<scope>/.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np

from .config import Config
from . import store

ACTIONS = ("merge", "archive", "condense", "keep")


def _reviewed_recently(row: sqlite3.Row, grace_days: int) -> bool:
    if not row["reviewed"]:
        return False
    return store.age_days(row["reviewed"]) < grace_days


def _clusters(ids: list[str], mat: np.ndarray, threshold: float) -> list[list[str]]:
    """Union-find over every pair above `threshold`."""
    parent = list(range(len(ids)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sims = mat @ mat.T
    n = len(ids)
    linked = False
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= threshold:
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
                linked = True
    if not linked:
        return []

    groups: dict[int, list[str]] = {}
    for i, mid in enumerate(ids):
        groups.setdefault(find(i), []).append(mid)
    return [g for g in groups.values() if len(g) > 1]


def report(cfg: Config, scopes: list[str] | None = None, conn: sqlite3.Connection | None = None) -> dict:
    owned = conn is None
    conn = conn or store.connect(cfg)
    try:
        c = cfg.compaction
        rows, _ = store.load_scope(conn, cfg, scopes, include_superseded=True)
        by_id = {r["id"]: r for r in rows}
        usage = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM usage")}

        def describe(mid: str) -> dict:
            row = by_id[mid]
            u = usage.get(mid, {})
            return {
                "id": mid,
                "scope": row["scope"],
                "type": row["type"],
                "importance": row["importance"],
                "created": row["created"],
                "tags": store.tags_of(row),
                "title": row["title"],
                "body": row["body"],
                "recalled_count": u.get("recalled_count", 0),
                "last_recalled": u.get("last_recalled"),
            }

        duplicates = []
        for scope in sorted({r["scope"] for r in rows}):
            live = [
                r for r in rows
                if r["scope"] == scope
                and r["superseded_by"] is None
                and not _reviewed_recently(r, c["reviewed_grace_days"])
            ]
            if len(live) < 2:
                continue
            ids = [r["id"] for r in live]
            mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in live])
            for cluster in _clusters(ids, mat, c["duplicate_threshold"]):
                duplicates.append(
                    {"kind": "duplicates", "scope": scope, "members": [describe(i) for i in cluster]}
                )

        stale = []
        for row in rows:
            if row["superseded_by"] is not None or _reviewed_recently(row, c["reviewed_grace_days"]):
                continue
            if (row["importance"] or 3) > c["stale_max_importance"]:
                continue
            if store.age_days(row["created"]) < c["stale_age_days"]:
                continue
            u = usage.get(row["id"], {})
            never = not u.get("recalled_count")
            cold = u.get("last_recalled") and store.age_days(u["last_recalled"]) > c["stale_last_recalled_days"]
            if never or cold:
                stale.append({"kind": "stale", "scope": row["scope"], "members": [describe(row["id"])]})

        superseded = [
            {"kind": "superseded", "scope": row["scope"], "superseded_by": row["superseded_by"],
             "members": [describe(row["id"])]}
            for row in rows
            if row["superseded_by"] is not None
            and not _reviewed_recently(row, c["reviewed_grace_days"])
        ]

        oversized = [
            {"kind": "oversized", "scope": row["scope"], "members": [describe(row["id"])]}
            for row in rows
            if row["superseded_by"] is None
            and len(row["body"] or "") > c["oversized_chars"]
            and not _reviewed_recently(row, c["reviewed_grace_days"])
        ]

        groups = duplicates + stale + superseded + oversized
        return {
            "scopes": scopes or cfg.scopes(),
            "total_memories": len([r for r in rows if r["superseded_by"] is None]),
            "candidates": len(groups),
            "groups": groups,
        }
    finally:
        if owned:
            conn.close()


def _oldest(values: list[str]) -> str:
    dated = [(store.parse_dt(v) or datetime.now(timezone.utc), v) for v in values if v]
    return min(dated)[1] if dated else store.now_iso()


def _validate(cfg: Config, plan: dict, rows: dict, force: bool) -> None:
    """Reject the whole plan before a single file moves.

    Compaction touches files one after another; a bad id in the third action
    must not leave the first two already archived with no way back. So every
    action is checked up front: known kind, ids that exist, no id used twice, a
    merge that is actually a merge, and members that share one scope. The
    persona scope guard runs here too, so a plan that reaches into another
    persona's scope is refused before anything happens.
    """
    persona = os.environ.get("PERSONA_NAME", "").strip()

    def guard(scope: str) -> None:
        if force or not persona or scope in ("shared", persona):
            return
        raise store.StoreError(
            f"refusing to write scope {scope!r} as persona {persona!r} (pass --force to override)"
        )

    touched: list[str] = []
    for action in plan.get("actions", []):
        kind = action.get("action")
        if kind not in ACTIONS:
            raise store.StoreError(f"unknown action {kind!r} (expected one of {ACTIONS})")

        if kind == "merge":
            members = action.get("members") or []
            if len(members) < 2:
                raise store.StoreError("merge needs at least two members")
            if not action.get("title") or not action.get("body"):
                raise store.StoreError("merge needs a title and a body")
            missing = [m for m in members if m not in rows]
            if missing:
                raise store.StoreError(f"merge references unknown ids: {missing}")
            member_scopes = {rows[m]["scope"] for m in members}
            if len(member_scopes) > 1:
                raise store.StoreError(f"merge members span scopes {sorted(member_scopes)}; merge within one scope")
            scope = action.get("scope") or next(iter(member_scopes))
            if scope not in member_scopes:
                raise store.StoreError(f"merge scope {scope!r} does not match members' scope {sorted(member_scopes)}")
            guard(scope)
            touched.extend(members)

        elif kind == "condense":
            mid = action.get("id")
            if mid not in rows:
                raise store.StoreError(f"condense references unknown id: {mid}")
            if not action.get("body"):
                raise store.StoreError("condense needs a body")
            guard(rows[mid]["scope"])
            touched.append(mid)

        else:  # archive or keep
            ids = action.get("ids") or ([action["id"]] if action.get("id") else [])
            if not ids:
                raise store.StoreError(f"{kind} needs at least one id")
            for mid in ids:
                if mid not in rows:
                    raise store.StoreError(f"{kind} references unknown id: {mid}")
                guard(rows[mid]["scope"])
            touched.extend(ids)

    dupes = {m for m in touched if touched.count(m) > 1}
    if dupes:
        raise store.StoreError(f"plan references the same id in more than one action: {sorted(dupes)}")


def apply(cfg: Config, plan: dict, conn: sqlite3.Connection | None = None, force: bool = False) -> dict:
    """Execute a reviewed plan, then reindex. Returns a per-action log.

    The plan is validated in full first (`_validate`); if anything is wrong no
    file is moved, so a partial, unloggable state is impossible.
    """
    owned = conn is None
    conn = conn or store.connect(cfg)
    log: list[dict] = []
    try:
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM memories")}
        _validate(cfg, plan, rows, force)

        for action in plan.get("actions", []):
            kind = action.get("action")

            if kind == "merge":
                members = action["members"]
                srcs = [rows[m] for m in members]
                scope = action.get("scope") or srcs[0]["scope"]
                tags = action.get("tags")
                if tags is None:
                    seen: list[str] = []
                    for r in srcs:
                        for t in store.tags_of(r):
                            if t not in seen:
                                seen.append(t)
                    tags = seen
                created = action.get("created") or _oldest([r["created"] for r in srcs])
                title = action["title"]
                mem = store.Memory(
                    id=action.get("id") or store.new_id(title, created),
                    scope=scope,
                    title=title,
                    body=action["body"],
                    type=action.get("type") or srcs[0]["type"],
                    tags=tags,
                    importance=action.get("importance") or max((r["importance"] or 3) for r in srcs),
                    author=action.get("author") or (srcs[0]["author"] or ""),
                    created=created,
                    source_session=action.get("source_session", "compaction"),
                    supersedes=", ".join(members),
                )
                path = store.write_memory(cfg, mem)
                archived = [str(store.archive(cfg, m, conn)) for m in members]
                log.append({"action": "merge", "id": mem.id, "path": str(path), "archived": archived})

            elif kind == "archive":
                ids = action.get("ids") or [action["id"]]
                paths = [str(store.archive(cfg, i, conn)) for i in ids]
                log.append({"action": "archive", "ids": ids, "paths": paths})

            elif kind == "condense":
                mid = action["id"]
                if mid not in rows:
                    raise store.StoreError(f"condense references unknown id: {mid}")
                path = cfg.root / rows[mid]["path"]
                mem = store.read_memory(path)
                mem.body = action["body"]
                if action.get("title"):
                    mem.title = action["title"]
                path.write_text(mem.render(), encoding="utf-8")
                log.append({"action": "condense", "id": mid, "path": str(path)})

            else:  # keep
                ids = action.get("ids") or [action["id"]]
                stamp = action.get("reviewed") or store.now_iso()
                for mid in ids:
                    if mid not in rows:
                        raise store.StoreError(f"keep references unknown id: {mid}")
                    store.set_field(cfg.root / rows[mid]["path"], "reviewed", stamp)
                log.append({"action": "keep", "ids": ids, "reviewed": stamp})

        stats = store.reindex(cfg, conn=conn)
        return {"applied": log, "index": stats}
    finally:
        if owned:
            conn.close()
