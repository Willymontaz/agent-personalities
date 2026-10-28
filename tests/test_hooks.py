"""The two hooks, run as Claude Code runs them: JSON on stdin, text on stdout.

The contract worth guarding is the failure one — a hook that crashes must still
exit 0 with empty stdout, because its stdout goes straight into the session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO, seed

from memlib import store

RECALL = REPO / ".claude" / "hooks" / "recall.py"
SESSION_START = REPO / ".claude" / "hooks" / "session_start.py"


def run_hook(script: Path, payload, repo: Path, tmpdir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERSONA_REPO_ROOT": str(repo), "TMPDIR": str(tmpdir)}
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script)], input=body, env=env, capture_output=True, text=True
    )


def seeded(cfg) -> None:
    seed(cfg, "shared", "Prefers terse code reviews",
         "W. wants review comments as short bullets: no preamble, no restating the code.",
         tags=["topics/code-review", "people/alex"], type="preference", importance=4)
    seed(cfg, "alice", "Alice calls him boss",
         "Alice's running joke is to call W. 'boss' whenever he asks for something quickly.",
         tags=["topics/humour"], type="relationship")
    store.reindex(cfg)


def test_recall_injects_a_block(repo, cfg, tmp_path):
    seeded(cfg)
    proc = run_hook(
        RECALL,
        {"prompt": "how should I phrase my code review comments today?",
         "agent_type": "alice", "session_id": "s1"},
        repo, tmp_path,
    )
    assert proc.returncode == 0
    assert '<recalled-memories persona="alice"' in proc.stdout
    assert "prefers-terse-code-reviews" in proc.stdout
    assert proc.stdout.rstrip().endswith("</recalled-memories>")


def test_recall_works_across_languages(repo, cfg, tmp_path):
    seeded(cfg)
    proc = run_hook(
        RECALL,
        {"prompt": "comment je devrais rédiger mes commentaires de revue de code ?",
         "agent_type": "alice", "session_id": "s-fr"},
        repo, tmp_path,
    )
    assert "prefers-terse-code-reviews" in proc.stdout


def test_recall_dedupes_within_a_session(repo, cfg, tmp_path):
    seeded(cfg)
    payload = {"prompt": "how should I phrase my code review comments today?",
               "agent_type": "alice", "session_id": "s2"}
    first = run_hook(RECALL, payload, repo, tmp_path)
    second = run_hook(RECALL, payload, repo, tmp_path)
    assert "recalled-memories" in first.stdout
    assert second.stdout.strip() == "", "the same memory must not be injected twice in a session"


def test_recall_respects_scope(repo, cfg, tmp_path):
    seeded(cfg)
    proc = run_hook(
        RECALL,
        {"prompt": "tell me about the running joke between us", "agent_type": "bob", "session_id": "s3"},
        repo, tmp_path,
    )
    assert "alice-calls-him-boss" not in proc.stdout, "bob must not see alice's private memory"


def test_recall_skips_short_prompts_and_slash_commands(repo, cfg, tmp_path):
    seeded(cfg)
    for payload in (
        {"prompt": "ok thanks", "agent_type": "alice", "session_id": "s4"},
        {"prompt": "/compact-memory shared please", "agent_type": "alice", "session_id": "s5"},
        {"prompt": "", "agent_type": "alice", "session_id": "s6"},
    ):
        proc = run_hook(RECALL, payload, repo, tmp_path)
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


def test_recall_records_usage(repo, cfg, tmp_path):
    seeded(cfg)
    run_hook(
        RECALL,
        {"prompt": "how should I phrase my code review comments today?",
         "agent_type": "alice", "session_id": "s7"},
        repo, tmp_path,
    )
    conn = store.connect(cfg)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM usage")]
    finally:
        conn.close()
    assert len(rows) == 1
    assert "prefers-terse-code-reviews" in rows[0]["id"]
    assert rows[0]["recalled_count"] == 1
    assert rows[0]["injected_sessions"] == 1


def test_hooks_survive_malformed_stdin(repo, tmp_path):
    for script in (RECALL, SESSION_START):
        proc = run_hook(script, "this is not json", repo, tmp_path)
        assert proc.returncode == 0, f"{script.name} must never fail the session"
        assert proc.stdout.strip() == "", f"{script.name} must print nothing on error"
        assert proc.stderr.strip(), f"{script.name} should log the problem to stderr"


def test_session_start_brief(repo, cfg, tmp_path):
    seeded(cfg)
    (repo / "personas" / "alice").mkdir(parents=True)
    (repo / "personas" / "alice" / "voice.md").write_text(
        "# Voice pool\n\nProse that must never be injected as a line.\n\n"
        + "".join(f"- line {i}\n" for i in range(15)),
        encoding="utf-8",
    )

    proc = run_hook(SESSION_START, {"agent_type": "alice", "source": "startup"}, repo, tmp_path)
    assert proc.returncode == 0
    assert '<persona-voice name="alice">' in proc.stdout
    assert "Prose that must never be injected" not in proc.stdout
    assert proc.stdout.count("\n- line ") == 5, "exactly five voice lines are sampled"
    assert "alice: 1" in proc.stdout and "shared: 1" in proc.stdout
    assert "alice-calls-him-boss" in proc.stdout
    assert "explicit yes" in proc.stdout


def test_session_start_without_a_persona(repo, cfg, tmp_path):
    seeded(cfg)
    proc = run_hook(SESSION_START, {"source": "startup"}, repo, tmp_path)
    assert proc.returncode == 0
    assert "persona-voice" not in proc.stdout
    assert 'persona="none"' in proc.stdout


def test_session_start_brief_hides_other_personas(repo, cfg, tmp_path):
    seed(cfg, "shared", "Shared thing", "A shared body.")
    seed(cfg, "alice", "Alice thing", "An alice body.")
    seed(cfg, "bob", "Bob thing", "A bob body.")
    store.reindex(cfg)

    proc = run_hook(SESSION_START, {"agent_type": "alice", "source": "startup"}, repo, tmp_path)
    assert proc.returncode == 0
    assert "alice:" in proc.stdout and "shared:" in proc.stdout
    assert "bob" not in proc.stdout, "alice's brief must not mention bob's scope"


def test_session_start_resets_marker_on_compact(repo, cfg, tmp_path):
    seeded(cfg)
    payload = {"prompt": "how should I phrase my code review comments today?",
               "agent_type": "alice", "session_id": "sc"}

    first = run_hook(RECALL, payload, repo, tmp_path)
    assert "recalled-memories" in first.stdout
    second = run_hook(RECALL, payload, repo, tmp_path)
    assert second.stdout.strip() == "", "same memory deduped within the session"

    # A context compaction drops the injected memory from the window, so the
    # session-start hook must clear the marker and make it injectable again.
    run_hook(SESSION_START, {"agent_type": "alice", "source": "compact", "session_id": "sc"},
             repo, tmp_path)
    third = run_hook(RECALL, payload, repo, tmp_path)
    assert "recalled-memories" in third.stdout, "compaction must re-arm recall for the session"
