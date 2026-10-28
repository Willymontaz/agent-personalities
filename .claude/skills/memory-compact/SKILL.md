---
name: compact-memory
description: Guided memory compaction — review duplicate, stale, superseded and oversized memories group by group and apply merge/archive/condense/keep. Use on "/compact-memory", "compact memory", "clean up memory", "range la mémoire", or when the session-start brief printed a compaction nudge.
---

# Guided compaction

Memory grows, and some of it turns out useless. This is the guided pass: the
CLI finds candidates, **you decide nothing alone** — the user answers per group
— and then one command applies the result. Nothing is ever deleted; everything
removed moves to `memory/_archive/<scope>/` and can be moved back.

## 1. Report

```bash
bin/memory compact --report --scope shared      # or --scope <you>
```

Default to the scope the user named. With no scope, ask which one. You may
compact `shared` from any persona, but **only your own persona scope** — never
another persona's.

The JSON comes back as `groups`, each with a `kind`:

| kind | what it means |
|---|---|
| `duplicates` | two or more memories in one scope above cosine 0.88 |
| `stale` | importance ≤ 2, older than 180 days, never recalled (or cold for 120 days) |
| `superseded` | already replaced but still sitting on disk |
| `oversized` | body over ~1200 characters — a candidate to condense |

## 2. Walk the groups

One group at a time. Show the members compactly — title, date, tags,
`recalled_count` — and propose **exactly one** action, with a reason:

- **merge** (usually for `duplicates`) — write the consolidated memory
  yourself and show it. Keep the most specific facts from every member, union
  the tags, take the highest importance and the oldest `created`. The members
  all go to `_archive`, and the new memory lists them in `supersedes`.
- **archive** (usually for `stale` and `superseded`) — move it out of the live
  set.
- **condense** (for `oversized`) — rewrite the body shorter, show the new text.
- **keep** — stamp `reviewed: <today>` so the group is not proposed again for
  90 days. This is the right answer for anything sentimental or rarely
  relevant but worth having.

The user answers per group, or says "apply all" to accept every proposal as
shown. A memory the user hesitates over is a **keep**, not an archive.

## 3. Apply

Build the plan and run it:

`--apply -` reads the plan from stdin, so it is one approved `bin/memory` call
with no separate file to write:

```bash
bin/memory compact --apply - <<'EOF'
{
  "actions": [
    {"action": "merge", "scope": "shared",
     "members": ["<id1>", "<id2>"],
     "type": "preference", "tags": ["topics/code-review"], "importance": 4,
     "title": "<consolidated title>",
     "body": "<consolidated body>"},
    {"action": "archive", "ids": ["<id3>"]},
    {"action": "condense", "id": "<id4>", "body": "<shorter body>"},
    {"action": "keep", "ids": ["<id5>"]}
  ]
}
EOF
```

A merge must keep its members in one scope; the plan is checked in full before
any file moves, so a bad id leaves everything untouched.

`--apply` performs the file moves and writes, then reindexes. It prints a log
of what happened. Report it in one or two sentences — how many merged,
archived, condensed, kept — not as raw JSON.

Only include actions the user agreed to. An unanswered group is left alone;
there is no harm in running the pass again next week.

## Restoring

If something was archived in error:

```bash
mv memory/_archive/<scope>/<file>.md memory/<scope>/
bin/memory reindex
```

Say so when the user seems unsure — knowing it is reversible makes the whole
pass easier to agree to.
