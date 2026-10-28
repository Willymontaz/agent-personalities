# agent-personalities

Claude Code sessions opened **as a persona**, each with a shared memory and its
own, retrieved by embedding rather than by keyword, and written only when you
say yes.

No framework, no service, no API key. A persona is a system prompt
(`.claude/agents/<name>.md`), the memory is markdown files under `memory/`, and
the retrieval is two hooks plus a local Ollama embedding model.

---

## 1. Prerequisites

| what | why | check |
|---|---|---|
| `/opt/local/bin/python3` with numpy | everything runs on it — hooks hardcode this interpreter | `/opt/local/bin/python3 -c "import numpy"` |
| Ollama | the embedding backend, running locally | `brew install ollama` |
| `bge-m3` pulled (~1.2 GB) | the multilingual embedding model | `bin/memory install` |

Then:

```bash
bin/memory install     # ollama pull bge-m3, then doctor
bin/memory doctor      # interpreter, daemon, model, dims, index freshness
```

`doctor` must be all `[ok  ]` before anything else. It checks the things that
otherwise fail quietly: a daemon that is not running, a model that is not
pulled, an embedding size that no longer matches the config, an index older
than the files.

**Changing the embedding model** — edit `model` and `dims` in
`memory/config.json`, then:

```bash
bin/memory install
bin/memory reindex --force     # old vectors are useless under a new model
/opt/local/bin/python3 tests/calibrate.py -o tests/calibration.md
```

Drop-in alternatives, same two fields: `qwen3-embedding:0.6b` (1024 dims, needs
`query_prefix` set — see `config.json`), `embeddinggemma` (768 dims, fastest),
`snowflake-arctic-embed2` (1024 dims). `nomic-embed-text` is English-only;
don't.

The tests run against the real backend, so re-run them after a model change
too.

## 2. Start a persona

```bash
bin/persona example
bin/persona --help        # lists the personas you have
```

The launcher starts Ollama if it is not up, waits up to 10 s, reindexes if the
files moved ahead of the index, runs `doctor`, and only then execs
`claude --agent example`. It **refuses to start** rather than open a session
with no recall — a persona that has silently forgotten everything is worse than
one that will not launch.

You will see `@example` in the session header. That agent file *replaces*
Claude Code's default system prompt, which is the point: the session is the
persona, not Claude wearing a hat. Tools still work.

**Once, on the first run**, accept the trust dialog Claude Code shows for this
directory. Until you do, the `permissions.allow` entries in
`.claude/settings.json` are ignored — you will see *"Ignoring 3
permissions.allow entries … this workspace has not been trusted"* and the
persona will stop on every `bin/memory` call waiting for approval. Accepting it
once is what makes storing a memory a single "yes" instead of two.

## 3. Create a persona

```bash
cp .claude/agents/example.md .claude/agents/rosa.md
mkdir -p personas/rosa
cp personas/example/voice.md personas/rosa/voice.md   # optional
bin/persona rosa                                      # creates memory/rosa/ on first run
```

Fill in `IDENTITY`, `VOICE`, `BEHAVIOUR`. Two things matter more than the rest:

- **`BEHAVIOUR` as conditional rules**, "when X, do Y" — not adjectives. "When
  the user is frustrated, give one next step and stop" beats "empathetic".
- **`personas/<name>/voice.md`** — a pool of ~15 sentences written exactly as
  the persona would say them. Five are sampled at random each session, so the
  flavour shifts instead of ossifying around the same three lines. A handful of
  real sentences fixes a speaking voice far better than any list of adjectives.
  The file is optional; without it the `VOICE` section of the agent file is all
  there is.

Do **not** add a `memory:` key to the agent frontmatter — Claude Code's own
agent memory would sit next to this store and the two would drift.

## 4. How memory works

```
memory/shared/      every persona reads it     — about you and the world
memory/<persona>/   only that persona reads it — about you and them
memory/_archive/    compacted away, never indexed, reversible
```

One memory is one markdown file with frontmatter (id, scope, type, tags,
importance, created, supersedes). The files are the source of truth;
`memory/.index.sqlite` is derived and gitignored, and `bin/memory reindex`
rebuilds it. Edit a memory in your editor whenever you like, then reindex.

**Your memories and personas are not synced through this repo.** The memory
files — `shared`, every persona scope, and `_archive` — are gitignored
alongside the index, and so are the personas you create: `.claude/agents/*.md`
and `personas/<name>/`. Git tracks only the setup scaffolding — the code, the
hooks and skills, `memory/config.json`, `memory/tags.yaml`, and the single
`example` persona (`.claude/agents/example.md`, `personas/example/`) you copy
from. Everything else is your private data, so back it up with your own
mechanism rather than expecting git to carry it. For memory, the `.md` files
plus `config.json` are all a restore needs — `bin/memory reindex` rebuilds the
index from them. (On a clone where any of this was committed before the rule
existed, untrack it once with `git rm -r --cached memory/shared memory/_archive
memory/<persona> .claude/agents/<persona>.md personas/<persona>`; the tracked
scaffolding above stays.)

**Reading** happens on its own, two ways:

- `SessionStart` prints the persona brief: sampled voice lines, its most recent
  memories, scope counts, and a compaction nudge when one is due;
- `UserPromptSubmit` embeds each prompt and injects the top 3 matches above the
  threshold as a `<recalled-memories>` block — deduped, so the same memory is
  not re-injected later in the session.

Prompts under 4 words and anything starting with `/` are skipped.

**Writing** never happens on its own:

1. the persona proposes a full draft at a natural pause and asks "Store this?";
2. you say yes — anything else, including silence, is a no;
3. it runs `bin/memory add`, which prints the new id and flags any
   near-duplicate above cosine 0.90 so you get a `supersede` instead of two
   memories saying the same thing.

`/remember <text>` proposes on demand. `/recall` searches deeper than
the automatic top-3. `/compact-memory` runs the housekeeping pass.

**Tags shape retrieval.** They are embedded as part of the memory text
(`title\ntags: …\nbody`) *and* used as a discrete boost at query time, so a
question about code review reaches a memory tagged `topics/code-review` even
when the body never says the words. Tags stay English kebab-case in every
language — that is what lets a French and an English memory share one.

**Language**: memories are written in the language of the exchange, never
translated. Queries work in either direction; see `tests/calibration.md` for
the measured numbers and the one known weak spot (cross-lingual *idiom*, as
opposed to paraphrase).

## 5. Maintenance

```bash
bin/memory list --scope shared            # newest first, with recall counts
bin/memory list --unused --older-than 180 # candidates for the bin
bin/memory show <id>                      # the file plus how often it was recalled
bin/memory search "<query>" -k 10 -v      # what the persona would have found
bin/memory supersede <old> <new>          # old stops matching, file stays
bin/memory archive <id>                   # moves to _archive, never deletes
bin/memory tags                           # vocabulary plus usage counts
bin/memory reindex [--force]
```

**Editing by hand** — change the `.md`, run `bin/memory reindex`. Only the
changed file is re-embedded: the cache key is (file sha, model, recipe).

**Tuning recall** — `retrieval.threshold` in `memory/config.json`, currently
`0.50`. Raise it if the persona keeps bringing up memories that are merely
adjacent; lower it if it fails to remember things it should. Re-run
`tests/calibrate.py` after changing it; the sweep table tells you what the
change costs.

**Compaction** — memory grows, and some of it turns out useless. Three
mechanisms, cheapest first:

1. *passive* — recency decay and the `usage` table push stale, never-recalled
   memories down the ranking on their own;
2. *nudge* — the session brief prints one line when there are more than 10
   candidates. Nothing happens without you;
3. *guided* — `/compact-memory [scope]`. The persona reports groups
   (duplicates above cosine 0.88, stale, superseded, oversized) and proposes
   one action per group: **merge** (one consolidated memory, members archived,
   listed in `supersedes`), **archive**, **condense**, or **keep** (stamps
   `reviewed:` so it is left alone for 90 days). You answer per group or "apply
   all".

Nothing is ever deleted. To restore something:

```bash
mv memory/_archive/shared/<file>.md memory/shared/
bin/memory reindex
```

You may compact `shared` from any persona, but only a persona's own scope from
that persona.

## 6. Troubleshooting

| symptom | cause / fix |
|---|---|
| no `<recalled-memories>` ever appears | `bin/memory doctor`; then run the hook by hand (below) |
| `ollama unreachable` | `ollama serve`, or `brew services start ollama` |
| `model bge-m3 … run bin/memory install` | the model was never pulled, or was removed |
| `returned N dims but config says M` | model changed — fix `dims`, then `bin/memory reindex --force` |
| `index freshness: stale` | `bin/memory reindex` |
| persona recalls nothing it should | lower `threshold`, or check the memory is in a scope it reads (`bin/memory list --scope <persona>`) |
| a memory keeps surfacing wrongly | `bin/memory show <id>`, then `archive` or `supersede` it |
| every `bin/memory` call asks for approval | the workspace is not trusted yet — see §2 |

Run a hook by hand exactly as Claude Code runs it:

```bash
echo '{"prompt":"how should I write review comments?","agent_type":"example","session_id":"t"}' \
  | .claude/hooks/recall.py

echo '{"agent_type":"example","source":"startup"}' \
  | .claude/hooks/session_start.py
```

Both hooks are built to fail silently *into the session* and loudly on stderr:
on any error they print nothing and exit 0, because their stdout goes straight
into the conversation. If one seems dead, look at stderr — `claude --debug`
shows it.

## 7. Tests

```bash
/opt/local/bin/python3 -m pytest tests/          # 48 tests, ~12 s
/opt/local/bin/python3 tests/calibrate.py        # retrieval quality table
```

The suite runs against the real Ollama backend on purpose — a memory store with
faked embeddings proves nothing about recall. It stops with instructions rather
than degrading if the daemon or the model is missing.

## Layout

```
AGENTS.md                   protocol every persona shares (CLAUDE.md just includes it)
bin/persona                 launcher: env check → reindex → claude --agent <name>
bin/memory                  the whole CLI
scripts/memlib/             config, embedder (Ollama), store (files + sqlite),
                            retrieve (scoring), compact
memory/config.json          model, dims, retrieval weights, compaction thresholds
memory/tags.yaml            controlled tag vocabulary (free tags allowed)
personas/<name>/voice.md    voice line pool, 5 sampled per session
.claude/agents/<name>.md    the personality itself
.claude/hooks/              session_start.py (brief), recall.py (per-prompt recall)
.claude/skills/             memory-store, memory-recall, memory-compact
tests/                      pytest suite + calibrate.py + calibration.md
```
