#!/opt/local/bin/python3
"""UserPromptSubmit hook: embed the prompt, inject the memories it matches.

Scopes are always `shared` plus the active persona (`agent_type`, which Claude
Code puts in the hook stdin when the session runs under `--agent`).

Contract: stdout is injected into the session, so on ANY failure this prints
nothing, logs to stderr and exits 0. A broken hook must never break a session.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "scripts"))

MIN_WORDS = 4


def load_seen(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(path: Path, ids: set[str]) -> None:
    try:
        path.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    prompt = (payload.get("prompt") or "").strip()
    persona = (payload.get("agent_type") or "").strip()
    session_id = str(payload.get("session_id") or "")

    if not prompt or prompt.startswith("/") or len(prompt.split()) < MIN_WORDS:
        return 0

    from memlib import config as configlib
    from memlib import hookutil, retrieve, store

    cfg = configlib.load()  # honours PERSONA_REPO_ROOT, else the repo this file sits in
    store.ensure_fresh(cfg)

    # Under `bin/persona`, PERSONA_NAME is exported; it backs up agent_type in
    # case the hook field is ever absent.
    persona = persona or os.environ.get("PERSONA_NAME", "").strip()

    scopes = ["shared"]
    if persona and persona != "shared" and cfg.scope_dir(persona).is_dir():
        scopes.append(persona)

    marker = hookutil.recall_marker(session_id)
    seen = load_seen(marker)

    conn = store.connect(cfg)
    try:
        hits = retrieve.search(cfg, prompt, scopes=scopes, conn=conn, exclude_ids=seen)
        if not hits:
            return 0
        store.record_recall(conn, [h.id for h in hits])
    finally:
        conn.close()

    save_seen(marker, seen | {h.id for h in hits})

    out = [f'<recalled-memories persona="{persona or "none"}" n="{len(hits)}">']
    out.extend(h.one_line() for h in hits)
    out.append("</recalled-memories>")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - never break the session
        print(f"recall hook: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(0)
