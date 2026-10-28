# Agent personalities with shared + per-persona RAG memory (Claude Code native)

## Context

The earlier study concluded: don't adopt Letta/Mem0, build it on Claude Code itself, because Claude Code is already the agent runtime and a persona is "a string in the system prompt + a memory write path". Goal now: implement it.

Requirements (from the request):
- Open an agent *as* a given personality (one persona per session, no switching).
- Each persona reads a **shared** memory and its **own** memory.
- Memory is vector/RAG based, with **tags** that shape both the embedding and retrieval.
- The agent **proposes** a memory at the end of an exchange and stores it **only on explicit yes**.

Verified Claude Code facts the design relies on (docs fetched 2026-09-28):
- `claude --agent <name>` runs `.claude/agents/<name>.md` as the main session; its body **replaces** the default Claude Code system prompt; `CLAUDE.md` still loads; `skills:` frontmatter preloads full skill content; `model:` per agent.
- Hooks get `agent_type` in stdin JSON when running under `--agent`, so hooks know the active persona with no env-var plumbing.
- `SessionStart` and `UserPromptSubmit` plain-text stdout is injected as context Claude sees.

Reusable on this machine (no API key):
- **Ollama 0.21.2** at `/opt/homebrew/bin/ollama` (Metal-accelerated on Apple Silicon; `llama3.2` and `nomic-embed-text` already pulled). Used as the embedding backend via `POST http://127.0.0.1:11434/api/embed`.
- Interpreter: `/opt/local/bin/python3` (numpy 2.5 present). Only stdlib + numpy needed; no onnxruntime, no tokenizers.
- Pattern only, from `a prior doc-embedding script`: cache key = (content sha, model id, recipe) so the index invalidates itself exactly when one changes.

## Language: French + English, mixed, at any time (user decision)

- **Embedding backend: Ollama.** Default model **`bge-m3`** (568M params, 1024 dims, 8k context, no query/passage prefixes, top-tier open multilingual dense retriever, FR↔EN cross-lingual). Chosen over the small ONNX e5 the first draft proposed because you asked for a stronger model and Ollama makes size irrelevant on this Mac. `memory/config.json` holds `{"backend":"ollama","model":"bge-m3","dims":1024}`; swapping is one edit + `bin/memory reindex --force`.
  Alternatives, same one-line swap: `qwen3-embedding:0.6b` (highest MTEB multilingual scores of the three, 1024 dims, needs an instruction prefix on queries which the embedder adds from config), `embeddinggemma` (300M, 768 dims, fastest), `snowflake-arctic-embed2` (568M, 1024 dims). `nomic-embed-text` (already pulled) is English-only and rejected for that reason.
- **Daemon handling**: `bin/memory install` = `ollama pull <model>`. `doctor` checks the daemon answers and the model is listed. `bin/persona` starts `ollama serve` in the background if `/api/version` does not answer, waits up to 10 s, then fails fast (never a silent no-recall session). Hooks embed one short string per prompt; with the model kept warm (`keep_alive` set to 30 min in each request) that is tens of milliseconds.
- **Memories are written in the language of the exchange** (FR or EN, the agent does not translate); titles likewise. Tags are language-neutral English kebab-case slugs (`topics/code-review`) so FR and EN memories share tags.
- **Personas answer in the language the user is currently using**, switching turn by turn (rule in `AGENTS.md`). Persona files may be authored in either language.
- Calibration set (Verification) includes FR queries against EN memories and the reverse.

## Assumptions (stated, not asked — adjust at review)

1. **Markdown files are the source of truth** (human-editable, same frontmatter spirit as your `~/.claude/.../memory/` files); SQLite is a derived, gitignored index rebuilt from them. The memory content itself is private and gitignored too — only `config.json` and `tags.yaml` are tracked (see §Repository layout).
2. **Automatic recall on every prompt** (top-3 above a threshold, deduped per session) **plus** an on-demand deeper search skill.
3. No `git init` and no ingestion of past transcripts in this pass (both listed under "later").

## Repository layout

```
agent-personalities/
├── CLAUDE.md                      # one line: @AGENTS.md
├── AGENTS.md                      # protocol shared by every persona (memory tools, propose→confirm→store rule, tag vocabulary)
├── bin/
│   ├── persona                    # launcher: verify env, reindex if stale, exec `claude --agent <name>`
│   └── memory                     # CLI: install | doctor | add | search | list | show | supersede | archive | compact | reindex | tags
├── scripts/memlib/
│   ├── config.py                  # loads memory/config.json (backend, model, dims, prefixes, retrieval weights)
│   ├── embedder.py                # Ollama /api/embed client (urllib, stdlib), batching, optional query prefix, L2-norm, daemon health check
│   ├── store.py                   # memory .md ⇄ frontmatter parsing, SQLite index (files, vectors as float32 blobs), tolerant reindex
│   ├── retrieve.py                # scoring: relevance (cos + tag boost, thresholded) then recency + importance ordering; scope filter
│   ├── compact.py                 # report candidates; apply a validated plan (merge/archive/condense/keep)
│   └── hookutil.py                # per-session recall-marker path, shared by both hooks
├── memory/
│   ├── config.json                # model block + retrieval weights/threshold + tag vocabulary path
│   ├── tags.yaml                  # controlled vocabulary: topics/, people/, projects/, plus free tags allowed
│   ├── shared/                    # gitignored; *.md memories visible to every persona (private data)
│   ├── <persona>/                 # gitignored; *.md memories private to that persona
│   ├── _archive/<scope>/          # gitignored; compacted-away memories, kept for the record, never indexed
│   └── .index.sqlite              # gitignored; `memories` table derived, `usage` table persistent
├── personas/<name>/voice.md       # gitignored (except example/); optional pool of ~15 sample sentences "as the persona would say them"; 5 sampled per session (see Voice lines)
├── README.md                      # setup/recall guide for you (see README section)
├── .claude/
│   ├── settings.json              # hooks + permission allow for bin/memory
│   ├── agents/<name>.md           # gitignored (except example.md); THE personality file (frontmatter + persona prompt)
│   ├── hooks/session_start.py     # persona brief: sampled voice lines, N most recent persona memories, memory counts
│   ├── hooks/recall.py            # UserPromptSubmit: embed prompt → top-k shared∪persona → inject block
│   └── skills/
│       ├── memory-store/SKILL.md  # propose→confirm→store protocol (preloaded via agent `skills:`; command stays /memory-store)
│       ├── memory-recall/SKILL.md # `name: recall` → /recall — explicit deeper search (top-10, any scope filter)
│       ├── memory-compact/SKILL.md# `name: compact-memory` → /compact-memory — guided merge/archive session (see Compaction)
│       └── remember/SKILL.md      # `name: remember` → /remember — user-initiated store, delegates to memory-store
└── tests/                         # pytest, run with /opt/local/bin/python3 -m pytest
```

## Data model

Memory file `memory/<scope>/<YYYY-MM-DD>-<slug>.md`:

```markdown
---
id: 2026-09-28-prefers-terse-reviews
scope: shared            # or the persona name
author: <persona>
type: preference         # fact | preference | decision | event | relationship | insight
tags: [topics/code-review, people/alex]
importance: 3            # 1–5
created: 2026-09-28T14:05:00+02:00
source_session: <session_id>
supersedes: <id|null>
---
One-line title
Self-contained body, 2–8 sentences, written so it makes sense with zero conversation context.
```

**Embedding input** (this is where tags become "meaningful"): `"{title}\ntags: {tags joined}\n{body}"` (bge-m3 needs no prefix; `config.query_prefix` / `passage_prefix` exist for models that do, e.g. qwen3-embedding) — tags are part of the embedded text, so a query about "code review" lands on memories tagged `topics/code-review` even if the body phrases it differently. Tags are additionally used as a discrete boost at query time.

SQLite index (`memory/.index.sqlite`): table `memories(id, scope, path, sha, type, importance, created, tags_json, title, vec BLOB float32[dims])`. Cache key = (file sha, model id, recipe) as in `embed.py`; `reindex` only re-embeds changed files. Second table `usage(id, recalled_count, last_recalled, injected_sessions)` is **not** derived from files and survives `reindex --force`; the recall hook increments it whenever it injects a memory. This is the "is it useful?" signal compaction relies on. Brute-force numpy cosine — thousands of memories is trivial.

**Ids are unique across every live scope, not just within one.** The index keys on `id` alone, so a `shared` and a persona memory with the same day+slug would collide and take reindex (and therefore recall) down; `write_memory` checks all scope dirs and suffixes `-2` on any collision. **Reindex is tolerant**: a file that cannot be parsed (e.g. a hand-edited `importance: high`) or a duplicate id is *skipped*, not fatal, and the reasons are recorded in a `meta.errors` row. `bin/memory doctor` and the session brief surface that row, so one bad hand-edit degrades to a warning rather than a silent no-recall session. A YAML-list `supersedes: [a, b]` is accepted and normalised.

## Retrieval

`retrieve.search(query, scopes=["shared", persona], k, threshold)`:
1. `cos = query_vec · memory_vec`
2. tag boost: `+0.05` per query keyword that matches a tag leaf (`code-review`), capped `+0.10`
3. **`relevance = cos + tag boost`** — what a memory is *about*; this is what the threshold acts on.
4. recency: `× (0.85 + 0.15·exp(−age_days/180))`; importance: `+0.02·(importance−3)` — these fold into `score`, which **only orders** the results.
5. drop below `threshold` **on `relevance`, not `score`** (start 0.50, calibrate — see Verification), drop superseded ids, sort by `score`, return top-k. Thresholding on `relevance` rather than the recency-weighted `score` is deliberate: an old-but-relevant memory must stay recallable rather than fall off a cliff as it ages.

Hook `recall.py` (UserPromptSubmit): read stdin JSON → `prompt`, `agent_type`, `session_id`; skip if prompt < 4 words or starts with `/`; scopes = `shared` + `agent_type` if present; dedupe against `$TMPDIR/persona-recall-<session_id>.json` of already-injected ids; print:

```
<recalled-memories persona="x" n="2">
- [2026-09-28-prefers-terse-reviews] (shared, preference, tags: …) Title — body
</recalled-memories>
```

Hook `session_start.py`: print the 5 sampled voice lines (if `personas/<name>/voice.md` exists), the 5 most recent persona memories, counts per scope, and a one-line reminder of the store protocol. Matcher `startup|resume|clear|compact`. Counts and the compaction nudge are restricted to the scopes this session may act on (`shared` + its own persona) — a persona is never shown or nudged about another's scope. On `source` `clear` or `compact` the hook deletes this session's recall marker, so memories dropped from the context window when it was cleared or compacted become injectable again. It also surfaces a one-line warning when the last reindex could not parse a file.

Both hooks: run with `/opt/local/bin/python3`, exit 0 with empty stdout on any error (never break the session), log to stderr.

## Write path (propose → confirm → store)

`.claude/skills/memory-store/SKILL.md` (preloaded into every persona via `skills: [memory-store]`):
- **When to propose**: at a natural pause after the user states a durable fact, preference, decision, or something emotionally/relationally significant; never mid-task; at most one proposal per exchange.
- **How to propose**: show the full draft in a fenced block (scope, type, tags from `memory/tags.yaml` first, then free tags; importance; title; body), then ask one question: "Store this?" Default scope = persona; propose `shared` only when it is about the user or the world, not about this persona's relationship.
- **On yes** (only on an explicit yes): run one command — `bin/memory add --scope … --type … --tags … --importance … --title "…" --author … --session … --body-file - <<'EOF' … EOF`. `--body-file -` reads the body from stdin via a here-doc, so the whole store is a single approved `bin/memory` call with no separate temp-file write for Claude Code to prompt about (a `cat > $TMPDIR/…` would prompt on every store). The CLI prints the new id and any near-duplicates (cos > 0.90). If a near-duplicate exists the agent proposes `bin/memory supersede <old> <new>` instead of leaving two.
- **On no / edit**: apply the edit and re-show, or drop it silently.
- `/remember <text>` is the user-initiated shortcut — its own skill (`.claude/skills/remember`, `name: remember`) that skips the "is this worth keeping?" judgement and delegates the draft/confirm/store to the memory-store skill. Still requires an explicit yes.

`AGENTS.md` carries the same rules in short form plus: "shared memory is read by every persona; never write another persona's scope". This last rule is also **enforced**, not merely asked for: `bin/persona` exports `PERSONA_NAME`, and `bin/memory` (`add`, `archive`, `supersede`, `compact --apply`) refuses to write any scope other than `shared` or that persona unless `--force` is passed. Outside a persona launch (plain `claude`, the tests) the env var is unset and nothing is guarded.

`bin/memory` CLI (Python, argparse): `install` (`ollama pull <model>` from config), `doctor` (interpreter + numpy, Ollama daemon reachable, model listed, embedding dims match config, index freshness — fail fast, never degrade silently, per your CLAUDE.md rule), `add`, `search --scope --k --threshold`, `list --scope [--unused --older-than]`, `show <id>`, `supersede <old> <new>`, `archive <id>…`, `compact --report|--apply <plan.json>`, `reindex [--force]`, `tags` (vocabulary + usage counts).

## Compaction (memory grows; some of it turns out useless)

Three mechanisms, cheapest first:

1. **Passive decay, always on**: the recency factor and the `usage` table already push stale, never-recalled memories down the ranking, so growth degrades relevance slowly rather than abruptly.
2. **Nudge**: the SessionStart hook runs `bin/memory compact --report --quiet`; if candidates exceed a config threshold (default 10) it prints one line: "memory: N compaction candidates, run /compact-memory when convenient". Nothing happens without you.
3. **Guided compaction: `/compact-memory [scope]`** (skill `memory-compact`). The persona runs `bin/memory compact --report --scope <shared|persona>` which returns JSON groups:
   - **duplicates**: clusters with pairwise cosine > 0.88 (threshold in config) inside one scope;
   - **stale**: importance ≤ 2, older than 180 days, `recalled_count` = 0 or `last_recalled` older than 120 days;
   - **superseded chains**: memories already replaced but still on disk;
   - **oversized**: bodies over ~1200 chars (candidates to condense).
   For each group the persona shows the members and proposes exactly one action — **merge** (writes one consolidated memory that keeps the most specific facts, union of tags, max importance, oldest `created`, and lists the members in `supersedes`), **archive** (move file to `memory/_archive/<scope>/`, drop from index, keep usage row), **condense** (rewrite one memory shorter), or **keep** (record a `reviewed: <date>` frontmatter field so it is not re-proposed for 90 days). You answer per group or "apply all"; the persona builds `plan.json` and runs `bin/memory compact --apply plan.json`, which performs the file moves/writes and reindexes. Nothing is ever deleted; the move to `_archive` is a local file move, so it is reversible.
   Compaction of `shared` may be run from any persona; compaction of a persona scope only from that persona (same rule as writes).

## Personality file

`.claude/agents/<name>.md`:

```markdown
---
name: <name>
description: <one line>
model: fable            # or opus/sonnet per persona
skills: [memory-store]
tools: Bash, Read, Glob, Grep
memory: none is fine — our RAG replaces Claude Code's agent-memory; do NOT set `memory:`
---
## IDENTITY
You are <nickname>. <relationship sentence>. <2–3 lines backstory>
## VOICE
<3 lines of actual dialogue, not adjectives>
## BEHAVIOUR (conditional rules: "when X, do Y")
…
## MEMORY
You receive <recalled-memories> blocks — treat them as your own recollection, cite naturally, never as "the system says".
Follow the memory-store skill to propose memories. Never store without an explicit yes.
```

Ship one fully written example persona (`.claude/agents/example.md` + `personas/example/voice.md`) as the template; you author the real ones.

### Voice lines (`personas/<name>/voice.md`)

A "voice line" is one sentence written exactly the way the persona would say it, e.g. `Bon, on regarde le vrai problème ou on continue à tourner autour ?`. Adjectives in a prompt ("dry, direct") produce a generic tone; a handful of real sentences fixes the actual speaking style far better. Keeping ~15 in a pool and injecting 5 **at random each session** (via the SessionStart hook) gives the persona a slightly different flavour every time instead of repeating the same three lines. This is the elizaOS trick noted in the earlier study. The file is optional: a persona without it just uses the VOICE section of its agent file. Lines may be FR, EN, or mixed.

## README.md (for you, kept short)

1. **Prerequisites**: `/opt/local/bin/python3` with numpy, Ollama installed (`brew install ollama`), then `bin/memory install` (pulls `bge-m3`, ~1.2 GB), `bin/memory doctor`. How to change the model (edit config, `install`, `reindex --force`).
2. **Start a persona**: `bin/persona <name>`; what the `@name` header means; how to list personas.
3. **Create a persona**: copy `.claude/agents/example.md`, fill IDENTITY/VOICE/BEHAVIOUR, optional `personas/<name>/voice.md`, run `bin/persona <name>`.
4. **How memory works**: files under `memory/shared` and `memory/<name>`, auto-recall on each prompt, the propose → "yes" → stored flow, `/recall` and `/remember`.
5. **Maintenance**: `bin/memory list|show|search|supersede|archive|reindex|tags`, editing a memory file by hand (then `reindex`), tuning `threshold` in `memory/config.json`, swapping the model. **Compaction**: when to run `/compact-memory`, what merge/archive/condense/keep do, where archived files go, how to restore one (move back + `reindex`).
6. **Troubleshooting**: hook prints nothing → run `doctor`, run the hook by hand with a JSON stdin sample; Ollama not answering → `ollama serve` or `brew services start ollama`; dims mismatch after a model change → `reindex --force`.

`bin/persona <name>`: ensure Ollama is up (start `ollama serve` detached if needed) → `doctor` → `reindex` if any .md newer than the index → `exec claude --agent <name>` from the repo root (so `.claude/agents` and hooks resolve). Trade-off noted: `--agent` replaces Claude Code's default system prompt, which is what we want for a conversational persona; tools still work from their schemas.

`.claude/settings.json`: hooks above; `permissions.allow`: `Bash(bin/memory *)`, `Bash(./bin/memory *)` so storing never prompts.

## Implementation steps

1. `memory/config.json` (backend ollama, model bge-m3, dims 1024, empty prefixes, retrieval weights), `bin/memory install` (`ollama pull bge-m3`, ~1.2 GB) and `doctor`.
2. `config.py`, `embedder.py` (urllib POST to `/api/embed` with `{"model","input":[...],"keep_alive":"30m"}`, L2-normalise, dims check). Test: cosine("Je préfère des revues courtes", "I prefer terse reviews") > cosine with an unrelated sentence, in both directions.
3. `store.py` + `retrieve.py` + `bin/memory` CLI, `memory/tags.yaml` seed, `.gitignore` for `.index.sqlite`.
4. Hooks `recall.py`, `session_start.py`; `.claude/settings.json`.
5. `AGENTS.md`, `CLAUDE.md`, skills `memory-store`, `memory-recall` (`name: recall`), `memory-compact` (`name: compact-memory`), `remember` (`name: remember`); `compact --report/--apply` and `archive` in the CLI; example persona + voice pool.
6. `bin/persona` launcher.
7. Tests + calibration (below). Write `README.md` per the outline above.

## Verification

- `bin/memory doctor` passes with `/opt/local/bin/python3`.
- `bin/memory install` then `doctor` reports daemon up, `bge-m3` present, dims 1024.
- `/opt/local/bin/python3 -m pytest tests/` (48 tests): FR↔EN cross-lingual similarity sanity (above); add→search roundtrip; scope isolation (persona A never sees persona B's memory, both see shared); supersede hides the old id; `compact --report` groups two near-identical seeded memories and flags a stale one; `compact --apply` with a merge plan yields one new file, moves members to `_archive`, and search returns only the merged id; usage counts survive `reindex --force`; hook returns empty stdout + exit 0 on malformed stdin. Added by the review: a cross-scope id collision indexes both files with distinct ids; a malformed hand-edited file (`importance: high`) is skipped, reported by `doctor`, and does not stop the good memories indexing; a YAML-list `supersedes` round-trips; a 900-day-old but relevant memory still clears the threshold (proving recency no longer gates recall); the session brief run as one persona never mentions another's scope; a compaction `source` re-arms recall for the session; `add --scope <other>` is refused under `PERSONA_NAME` and allowed with `--force`; `compact --apply` with one bad id, a cross-scope merge, or a one-member merge changes nothing on disk; `keep` silences a superseded group.
- Hook smoke test: `echo '{"prompt":"…","agent_type":"example","session_id":"t"}' | .claude/hooks/recall.py` prints a `<recalled-memories>` block.
- Threshold calibration: seed ~15 memories across shared/example, half in French, half in English; run 12 hand-written queries (FR→EN, EN→FR, same-language hits, and negatives in both languages), tune `threshold` in `config.json` until no false positives on the negatives; record the table in `tests/calibration.md`.
- End-to-end: `bin/persona example`, confirm `@example` in the header, ask something matching a seeded memory, see it recalled; state a preference, get a proposal, say yes, verify the new `.md` file and that `bin/memory search` finds it.

## Later (not in this pass)

- `git init` and first commit (only on your say-so, per your commit rule).
- Bulk ingestion of past Claude Code transcripts into `shared` (the earlier study's "extraction pass").
- Scheduled compaction (cron/`/loop`) once the manual `/compact-memory` flow has proven its thresholds.
