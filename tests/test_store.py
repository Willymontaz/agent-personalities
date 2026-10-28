"""Files on disk, the index derived from them, and what search may return."""

from __future__ import annotations

from conftest import days_ago, run_cli, seed

from memlib import retrieve, store


def test_frontmatter_roundtrip(cfg):
    mem = seed(
        cfg, "shared", "Préfère les revues courtes",
        "W. veut des commentaires de revue en puces brèves, sans préambule.",
        tags=["topics/code-review", "people/alex"], type="preference", importance=4,
    )
    back = store.read_memory(mem.path)
    assert back.id == mem.id
    assert back.scope == "shared"
    assert back.title == "Préfère les revues courtes"
    assert back.tags == ["topics/code-review", "people/alex"]
    assert back.importance == 4
    assert back.type == "preference"
    assert back.body.startswith("W. veut")


def test_add_then_search_roundtrip(repo, cfg):
    code, out = run_cli(
        repo, "add", "--scope", "shared", "--type", "preference",
        "--tags", "topics/code-review,people/alex", "--importance", "4",
        "--title", "Prefers terse code reviews",
        "--body", "W. wants review comments as short bullets: no preamble, no restating the code.",
    )
    assert code == 0
    assert "stored " in out

    hits = retrieve.search(cfg, "how should I write review comments?", scopes=["shared"], k=3)
    assert hits, "the memory just added did not come back"
    assert hits[0].title == "Prefers terse code reviews"
    assert hits[0].cos > 0.5


def test_search_is_cross_lingual(cfg):
    seed(cfg, "shared", "Prefers terse code reviews",
         "W. wants review comments as short bullets, no preamble, no restating the code.",
         tags=["topics/code-review"], type="preference")
    store.reindex(cfg)

    hits = retrieve.search(cfg, "comment rédiger mes commentaires de revue de code ?", scopes=["shared"])
    assert hits and hits[0].title == "Prefers terse code reviews"


def test_negative_query_stays_below_threshold(cfg):
    seed(cfg, "shared", "Prefers terse code reviews", "Short bullets, no preamble.",
         tags=["topics/code-review"], type="preference")
    store.reindex(cfg)

    assert retrieve.search(cfg, "quelle est la météo à Lisbonne demain matin ?", scopes=["shared"]) == []


def test_scope_isolation(cfg):
    seed(cfg, "shared", "Lives in Paris", "W. is based in Paris and works European hours.",
         tags=["people/alex"])
    seed(cfg, "alice", "Alice calls him boss", "Alice's running joke is to call W. 'boss'.",
         tags=["topics/humour"], type="relationship")
    seed(cfg, "bob", "Bob keeps a haiku count", "Bob has answered W. in haiku eleven times.",
         tags=["topics/humour"], type="relationship")
    store.reindex(cfg)

    def ids(query, scopes):
        return {h.id for h in retrieve.search(cfg, query, scopes=scopes, k=10, threshold=0.0)}

    alice_view = ids("running joke", ["shared", "alice"])
    bob_view = ids("running joke", ["shared", "bob"])

    assert any("alice-calls" in i for i in alice_view)
    assert not any("haiku" in i for i in alice_view), "alice saw bob's private memory"
    assert any("haiku" in i for i in bob_view)
    assert not any("alice-calls" in i for i in bob_view), "bob saw alice's private memory"

    shared_id = next(i for i in alice_view if "paris" in i)
    assert shared_id in bob_view, "shared memory is not visible to both personas"


def test_supersede_hides_the_old_id(repo, cfg):
    old = seed(cfg, "shared", "Works at Acme in Paris", "W. works at Acme, Paris office.")
    new = seed(cfg, "shared", "Works at Acme, now remote from Lyon",
               "W. still works at Acme but has moved to Lyon and works remotely.")
    store.reindex(cfg)

    code, _ = run_cli(repo, "supersede", old.id, new.id)
    assert code == 0

    ids = {h.id for h in retrieve.search(cfg, "where does he work", scopes=["shared"], k=10, threshold=0.0)}
    assert new.id in ids
    assert old.id not in ids

    ids_all = {
        h.id
        for h in retrieve.search(
            cfg, "where does he work", scopes=["shared"], k=10, threshold=0.0, include_superseded=True
        )
    }
    assert old.id in ids_all, "superseded memories must remain retrievable on demand"
    assert (cfg.scope_dir("shared") / f"{old.id}.md").exists(), "supersede must not delete the file"


def test_usage_survives_forced_reindex(cfg):
    mem = seed(cfg, "shared", "Prefers terse code reviews", "Short bullets, no preamble.")
    store.reindex(cfg)

    conn = store.connect(cfg)
    try:
        store.record_recall(conn, [mem.id])
        store.record_recall(conn, [mem.id])
        before = store.usage_of(conn, mem.id)
    finally:
        conn.close()
    assert before["recalled_count"] == 2
    assert before["injected_sessions"] == 2

    store.reindex(cfg, force=True)

    conn = store.connect(cfg)
    try:
        after = store.usage_of(conn, mem.id)
    finally:
        conn.close()
    assert after["recalled_count"] == 2
    assert after["injected_sessions"] == 2
    assert after["last_recalled"] == before["last_recalled"]


def counts_of(stats: dict) -> dict:
    return {k: stats[k] for k in ("total", "embedded", "reused")}


def test_reindex_reuses_unchanged_vectors(cfg):
    seed(cfg, "shared", "One", "First memory body.")
    seed(cfg, "shared", "Two", "Second memory body.")
    first = store.reindex(cfg)
    assert counts_of(first) == {"total": 2, "embedded": 2, "reused": 0}
    assert first["errors"] == []

    second = store.reindex(cfg)
    assert counts_of(second) == {"total": 2, "embedded": 0, "reused": 2}

    seed(cfg, "shared", "Three", "Third memory body.")
    third = store.reindex(cfg)
    assert counts_of(third) == {"total": 3, "embedded": 1, "reused": 2}


def test_model_change_invalidates_the_index(cfg):
    seed(cfg, "shared", "One", "First memory body.")
    store.reindex(cfg)
    assert not store.index_is_stale(cfg)

    cfg.data["recipe"] = "v2"
    assert store.index_is_stale(cfg), "a recipe change must invalidate the index"


def test_archive_moves_the_file_and_drops_it_from_search(repo, cfg):
    mem = seed(cfg, "shared", "An old note", "Something that stopped being true.")
    store.reindex(cfg)

    code, _ = run_cli(repo, "archive", mem.id)
    assert code == 0

    assert not (cfg.scope_dir("shared") / f"{mem.id}.md").exists()
    assert (cfg.archive_dir / "shared" / f"{mem.id}.md").exists(), "archive must never delete"
    assert retrieve.search(cfg, "old note", scopes=["shared"], k=10, threshold=0.0) == []


def test_add_flags_a_near_duplicate(repo, cfg):
    seed(cfg, "shared", "Prefers terse code reviews",
         "W. wants review comments as short bullets: no preamble, no restating the code.",
         tags=["topics/code-review"], type="preference")
    store.reindex(cfg)

    code, out = run_cli(
        repo, "add", "--scope", "shared", "--type", "preference",
        "--tags", "topics/code-review", "--importance", "4",
        "--title", "Prefers terse code reviews",
        "--body", "W. wants review comments as short bullets: no preamble, no restating the code.",
    )
    assert code == 0
    assert "near-duplicates" in out
    assert "supersede" in out


def test_add_rejects_an_empty_body(repo):
    code, _ = run_cli(repo, "add", "--scope", "shared", "--type", "fact", "--title", "x", "--body", "  ")
    assert code == 2


def test_tag_boost_is_capped(cfg):
    r = cfg.retrieval
    tags = ["topics/code-review", "topics/testing", "people/alex"]
    kws = retrieve.keywords("code review testing alex")
    boost = retrieve.tag_boost(tags, kws, r["tag_boost_per_match"], r["tag_boost_cap"])
    assert boost == r["tag_boost_cap"]
    assert retrieve.tag_boost(tags, retrieve.keywords("weather in Lisbon"), 0.05, 0.10) == 0.0


def test_recency_pushes_an_old_memory_down(cfg):
    fresh = seed(cfg, "shared", "Deployment window", "Deploys happen Tuesday afternoons.",
                 created=days_ago(1))
    old = seed(cfg, "shared", "Deployment window, old note", "Deploys happen Tuesday afternoons.",
               created=days_ago(900))
    store.reindex(cfg)

    hits = retrieve.search(cfg, "when do we deploy", scopes=["shared"], k=10, threshold=0.0)
    ranked = [h.id for h in hits]
    assert ranked.index(fresh.id) < ranked.index(old.id)


def test_ids_are_unique_across_scopes(repo, cfg):
    # Same title, same day, two scopes: the second must not collide on the id
    # (SQLite keys on id alone, so a collision would take reindex — and recall — down).
    code_a, _ = run_cli(repo, "add", "--scope", "shared", "--type", "fact",
                        "--title", "Shared thing", "--body", "A shared body.")
    code_b, _ = run_cli(repo, "add", "--scope", "alice", "--type", "fact",
                        "--title", "Shared thing", "--body", "An alice body.")
    assert code_a == 0 and code_b == 0

    conn = store.connect(cfg)
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM memories")]
    finally:
        conn.close()
    assert len(ids) == 2 and len(set(ids)) == 2, "ids collided across scopes"


def test_malformed_file_is_skipped_not_fatal(repo, cfg):
    good = seed(cfg, "shared", "Good note", "A perfectly fine memory.")
    bad = cfg.scope_dir("shared") / "2020-01-01-bad.md"
    bad.write_text(
        "---\nid: 2020-01-01-bad\nscope: shared\ntype: fact\ntags: []\n"
        "importance: high\n---\nBad note\nBody.\n",
        encoding="utf-8",
    )
    stats = store.reindex(cfg, force=True)
    assert stats["errors"], "the malformed file must be reported, not fatal"
    assert store.index_errors(cfg), "the error must be persisted for doctor/brief"

    conn = store.connect(cfg)
    try:
        ids = {r["id"] for r in conn.execute("SELECT id FROM memories")}
    finally:
        conn.close()
    assert good.id in ids, "a good memory beside a bad one must still index"
    assert "2020-01-01-bad" not in ids

    code, out = run_cli(repo, "doctor")
    assert code == 1
    assert "could not be indexed" in out


def test_list_style_supersedes_roundtrips(cfg):
    path = cfg.scope_dir("shared") / "2026-01-01-x.md"
    path.write_text(
        "---\nid: 2026-01-01-x\nscope: shared\ntype: fact\ntags: []\n"
        "importance: 3\nsupersedes: [a, b]\n---\nTitle\nBody.\n",
        encoding="utf-8",
    )
    mem = store.read_memory(path)
    assert mem.supersedes == "a, b"
    assert store.split_ids(mem.supersedes) == ["a", "b"]
    store.reindex(cfg)  # must not raise on a YAML-list supersedes


def test_threshold_ignores_age(cfg):
    import math

    old = seed(cfg, "shared", "Works at Acme, Paris office",
               "W. is a software engineer at Acme and works out of the Paris office on European hours.",
               tags=["people/alex"], created=days_ago(900))
    store.reindex(cfg)

    hits = retrieve.search(cfg, "où est-ce qu'il travaille et dans quelle ville ?", scopes=["shared"])
    assert any(h.id == old.id for h in hits), "an old but relevant memory must still be recalled"

    h = next(h for h in hits if h.id == old.id)
    r = cfg.retrieval
    recency = r["recency_base"] + r["recency_span"] * math.exp(
        -store.age_days(h.created) / r["recency_scale_days"]
    )
    assert h.relevance >= r["threshold"]
    # The old policy thresholded the recency-weighted score; this memory misses it,
    # which is exactly the cliff the change removes.
    assert h.relevance * recency < r["threshold"], \
        "test no longer exercises the age cliff — pick a lower-cos query"


def test_scope_guard_blocks_other_persona(repo, monkeypatch):
    monkeypatch.setenv("PERSONA_NAME", "alice")
    blocked, _ = run_cli(repo, "add", "--scope", "bob", "--type", "fact",
                         "--title", "x", "--body", "some body")
    assert blocked == 2, "alice must not write bob's scope"

    forced, _ = run_cli(repo, "add", "--scope", "bob", "--type", "fact",
                        "--title", "x", "--body", "some body", "--force")
    assert forced == 0, "--force overrides the guard"

    own, _ = run_cli(repo, "add", "--scope", "alice", "--type", "fact",
                     "--title", "y", "--body", "some body")
    shared, _ = run_cli(repo, "add", "--scope", "shared", "--type", "fact",
                        "--title", "z", "--body", "some body")
    assert own == 0 and shared == 0, "own and shared scopes stay writable"

