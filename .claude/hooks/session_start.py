#!/opt/local/bin/python3
"""SessionStart hook: the persona's opening brief.

Prints, for the persona named by `agent_type`:
  - 5 voice lines sampled at random from personas/<name>/voice.md (a different
    flavour every session instead of the same three lines every time);
  - its 5 most recent private memories;
  - memory counts per scope;
  - the one rule about storing;
  - a compaction nudge, only when candidates pass the configured threshold.

Same contract as recall.py: never break a session, stderr for problems, exit 0.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "scripts"))

VOICE_SAMPLE = 5
RECENT = 5


def voice_lines(root: Path, persona: str) -> list[str]:
    path = root / "personas" / persona / "voice.md"
    if not path.is_file():
        return []
    # Only `- ` bullets are voice lines; anything else in the file is prose
    # explaining the pool to a human and must not be injected.
    return [
        raw.strip()[2:].strip()
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip().startswith("- ") and raw.strip()[2:].strip()
    ]


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    persona = (payload.get("agent_type") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    source = (payload.get("source") or "").strip()

    from memlib import config as configlib
    from memlib import compact as compaction
    from memlib import hookutil, store

    cfg = configlib.load()  # honours PERSONA_REPO_ROOT, else the repo this file sits in
    store.ensure_fresh(cfg)

    persona = persona or os.environ.get("PERSONA_NAME", "").strip()

    # /clear and context compaction drop the injected memories from the window,
    # so they must become injectable again: wipe the per-session recall marker.
    if source in ("clear", "compact") and session_id:
        try:
            hookutil.recall_marker(session_id).unlink(missing_ok=True)
        except OSError:
            pass

    # The scopes this session may actually read and act on: shared plus its own.
    scopes = ["shared"]
    if persona and persona != "shared" and cfg.scope_dir(persona).is_dir():
        scopes.append(persona)

    out: list[str] = []

    pool = voice_lines(cfg.root, persona) if persona else []
    if pool:
        sample = random.sample(pool, min(VOICE_SAMPLE, len(pool)))
        out.append(f"<persona-voice name=\"{persona}\">")
        out.append("Speak like this — these are your own lines, not a script to quote:")
        out.extend(f"- {line}" for line in sample)
        out.append("</persona-voice>")

    conn = store.connect(cfg)
    try:
        placeholders = ",".join("?" * len(scopes))
        counts = {
            r["scope"]: r["n"]
            for r in conn.execute(
                "SELECT scope, COUNT(*) n FROM memories "
                f"WHERE superseded_by IS NULL AND scope IN ({placeholders}) GROUP BY scope",
                scopes,
            )
        }
        recent = []
        if persona:
            recent = list(
                conn.execute(
                    "SELECT id, type, title, tags_json, created FROM memories "
                    "WHERE scope = ? AND superseded_by IS NULL ORDER BY created DESC LIMIT ?",
                    (persona, RECENT),
                )
            )
    finally:
        conn.close()

    out.append(f"<persona-memory-brief persona=\"{persona or 'none'}\">")
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no memories yet"
    out.append(f"Memory scopes in play: {summary}.")
    if recent:
        out.append(f"Your {len(recent)} most recent private memories:")
        for r in recent:
            tags = ", ".join(t for t in (r["tags_json"] or "").split("\n") if t) or "none"
            out.append(f"- [{r['id']}] ({r['type']}, tags: {tags}) {r['title']}")
    out.append(
        "Relevant memories arrive as <recalled-memories> blocks during the conversation; "
        "treat them as your own recollection."
    )
    out.append(
        "Never store a memory without an explicit yes from the user — propose first "
        "(memory-store skill), store only on confirmation."
    )
    if session_id:
        # A persona cannot read its own session id; only hooks get it. Pass it
        # back so `bin/memory add --session` can fill in source_session.
        out.append(f"When you store a memory, pass --session {session_id} to bin/memory add.")

    errors = store.index_errors(cfg)
    if errors:
        out.append(
            f"memory: {len(errors)} file(s) could not be indexed — run `bin/memory doctor`."
        )

    try:
        rep = compaction.report(cfg, scopes=scopes)
        threshold = cfg.compaction["nudge_threshold"]
        if rep["candidates"] >= threshold:
            out.append(
                f"memory: {rep['candidates']} compaction candidates, run /compact-memory when convenient."
            )
    except Exception as exc:  # noqa: BLE001 - a nudge is never worth a broken brief
        print(f"session_start hook: compaction report skipped: {exc}", file=sys.stderr)

    out.append("</persona-memory-brief>")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - never break the session
        print(f"session_start hook: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(0)
