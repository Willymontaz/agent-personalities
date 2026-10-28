"""Compaction: what the report flags, and what applying a plan actually does."""

from __future__ import annotations

import json

from conftest import days_ago, run_cli, seed

from memlib import compact as compaction
from memlib import retrieve, store


def groups_of(report: dict, kind: str) -> list[dict]:
    return [g for g in report["groups"] if g["kind"] == kind]


def test_report_clusters_near_identical_memories(cfg):
    a = seed(cfg, "shared", "Prefers terse code reviews",
             "W. wants review comments as short bullets: no preamble, no restating the code.",
             tags=["topics/code-review"], type="preference")
    b = seed(cfg, "shared", "Wants short review comments",
             "W. asks for review comments kept to brief bullets, with no preamble and no restatement "
             "of the code under review.",
             tags=["topics/code-review"], type="preference")
    seed(cfg, "shared", "Allergic to shellfish",
         "W. cannot eat shellfish; it is a real allergy, not a preference.", tags=["topics/food"])
    store.reindex(cfg)

    dupes = groups_of(compaction.report(cfg), "duplicates")
    assert len(dupes) == 1
    members = {m["id"] for m in dupes[0]["members"]}
    assert members == {a.id, b.id}


def test_report_flags_a_stale_memory(cfg):
    stale = seed(cfg, "shared", "Tried a standing desk in 2024",
                 "W. tried a standing desk for two weeks and went back to sitting.",
                 importance=1, created=days_ago(400))
    seed(cfg, "shared", "Allergic to shellfish", "A real allergy.", importance=5,
         created=days_ago(400))
    store.reindex(cfg)

    flagged = groups_of(compaction.report(cfg), "stale")
    assert [g["members"][0]["id"] for g in flagged] == [stale.id]


def test_reviewed_memory_is_not_re_proposed(cfg):
    stale = seed(cfg, "shared", "Tried a standing desk in 2024", "Two weeks, then back to sitting.",
                 importance=1, created=days_ago(400))
    store.reindex(cfg)
    assert groups_of(compaction.report(cfg), "stale")

    store.set_field(cfg.scope_dir("shared") / f"{stale.id}.md", "reviewed", days_ago(5))
    store.reindex(cfg)
    assert not groups_of(compaction.report(cfg), "stale")


def test_report_flags_superseded_and_oversized(cfg):
    old = seed(cfg, "shared", "Works in Paris", "W. works from the Paris office.")
    seed(cfg, "shared", "Works remotely from Lyon", "W. has moved to Lyon and works remotely.",
         supersedes=old.id)
    big = seed(cfg, "shared", "Long note", "x " * 800)
    store.reindex(cfg)

    report = compaction.report(cfg)
    assert [g["members"][0]["id"] for g in groups_of(report, "superseded")] == [old.id]
    assert big.id in {g["members"][0]["id"] for g in groups_of(report, "oversized")}


def test_apply_merge_consolidates_and_archives(cfg):
    a = seed(cfg, "shared", "Prefers terse code reviews", "Short bullets, no preamble.",
             tags=["topics/code-review"], type="preference", importance=3, created=days_ago(200))
    b = seed(cfg, "shared", "Wants short review comments", "Brief bullets, no restatement of the code.",
             tags=["people/alex"], type="preference", importance=5, created=days_ago(10))
    store.reindex(cfg)

    plan = {
        "actions": [
            {
                "action": "merge",
                "scope": "shared",
                "members": [a.id, b.id],
                "title": "Wants review comments as terse bullets",
                "body": "W. wants review comments as short bullets: no preamble, no restating the "
                        "code, and any requested length respected strictly.",
            }
        ]
    }
    result = compaction.apply(cfg, plan)
    merged_id = result["applied"][0]["id"]

    merged = store.read_memory(cfg.scope_dir("shared") / f"{merged_id}.md")
    assert merged.tags == ["topics/code-review", "people/alex"], "tags must be unioned"
    assert merged.importance == 5, "importance must be the max of the members"
    assert merged.created == a.created, "created must be the oldest member's"
    assert {s.strip() for s in merged.supersedes.split(",")} == {a.id, b.id}

    for member in (a, b):
        assert not (cfg.scope_dir("shared") / f"{member.id}.md").exists()
        assert (cfg.archive_dir / "shared" / f"{member.id}.md").exists()

    ids = {h.id for h in retrieve.search(cfg, "review comments", scopes=["shared"], k=10, threshold=0.0)}
    assert ids == {merged_id}


def test_apply_archive_condense_and_keep(repo, cfg, tmp_path):
    gone = seed(cfg, "shared", "Obsolete note", "No longer true.")
    long = seed(cfg, "shared", "Verbose note", "y " * 700)
    kept = seed(cfg, "shared", "Sentimental note", "Worth keeping even if rarely useful.",
                importance=1, created=days_ago(400))
    store.reindex(cfg)

    plan = {
        "actions": [
            {"action": "archive", "ids": [gone.id]},
            {"action": "condense", "id": long.id, "body": "Short version."},
            {"action": "keep", "ids": [kept.id]},
        ]
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    code, _ = run_cli(repo, "compact", "--apply", str(plan_file))
    assert code == 0

    assert (cfg.archive_dir / "shared" / f"{gone.id}.md").exists()
    assert store.read_memory(cfg.scope_dir("shared") / f"{long.id}.md").body == "Short version."
    assert store.read_memory(cfg.scope_dir("shared") / f"{kept.id}.md").reviewed
    assert not groups_of(compaction.report(cfg), "stale")


def test_apply_rejects_an_unknown_action(cfg):
    seed(cfg, "shared", "A note", "Body.")
    store.reindex(cfg)
    try:
        compaction.apply(cfg, {"actions": [{"action": "delete", "ids": ["whatever"]}]})
    except store.StoreError as exc:
        assert "delete" in str(exc)
    else:
        raise AssertionError("apply must refuse an unknown action")


def test_quiet_report_only_speaks_above_the_threshold(repo, cfg):
    seed(cfg, "shared", "A note", "Body.")
    store.reindex(cfg)
    code, out = run_cli(repo, "compact", "--quiet")
    assert code == 0 and out.strip() == ""

    for n in range(12):
        seed(cfg, "shared", f"Old note {n}", "Something nobody ever asked about again.",
             importance=1, created=days_ago(400))
    store.reindex(cfg)
    code, out = run_cli(repo, "compact", "--quiet")
    assert code == 0
    assert "compaction candidates" in out


def test_apply_is_atomic_on_a_bad_id(cfg):
    a = seed(cfg, "shared", "Keep me", "Body a.")
    seed(cfg, "shared", "Also keep", "Body b.")
    store.reindex(cfg)

    plan = {"actions": [
        {"action": "archive", "ids": [a.id]},
        {"action": "archive", "ids": ["does-not-exist"]},
    ]}
    try:
        compaction.apply(cfg, plan)
    except store.StoreError:
        pass
    else:
        raise AssertionError("apply must reject a plan that references an unknown id")

    # The first action must NOT have run: a bad id anywhere aborts the whole plan.
    assert (cfg.scope_dir("shared") / f"{a.id}.md").exists()
    assert not (cfg.archive_dir / "shared" / f"{a.id}.md").exists()


def test_apply_rejects_a_cross_scope_merge(cfg):
    a = seed(cfg, "shared", "A note", "Body a.")
    b = seed(cfg, "alice", "B note", "Body b.")
    store.reindex(cfg)

    plan = {"actions": [
        {"action": "merge", "members": [a.id, b.id], "title": "m", "body": "merged"},
    ]}
    try:
        compaction.apply(cfg, plan)
    except store.StoreError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("apply must refuse to merge across scopes")
    assert (cfg.scope_dir("shared") / f"{a.id}.md").exists()
    assert (cfg.scope_dir("alice") / f"{b.id}.md").exists()


def test_apply_rejects_a_thin_merge(cfg):
    a = seed(cfg, "shared", "A note", "Body a.")
    store.reindex(cfg)
    plan = {"actions": [{"action": "merge", "members": [a.id], "title": "m", "body": "merged"}]}
    try:
        compaction.apply(cfg, plan)
    except store.StoreError as exc:
        assert "two" in str(exc)
    else:
        raise AssertionError("a merge of one member is not a merge")


def test_keep_silences_a_superseded_group(cfg):
    old = seed(cfg, "shared", "Works in Paris", "W. works from the Paris office.")
    seed(cfg, "shared", "Works remotely from Lyon", "W. has moved to Lyon and works remotely.",
         supersedes=old.id)
    store.reindex(cfg)
    assert groups_of(compaction.report(cfg), "superseded"), "the old memory should be flagged"

    compaction.apply(cfg, {"actions": [{"action": "keep", "ids": [old.id]}]})
    assert not groups_of(compaction.report(cfg), "superseded"), \
        "keep must stop a superseded memory being re-proposed"
