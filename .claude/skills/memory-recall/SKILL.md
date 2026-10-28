---
name: recall
description: Deliberate, deeper memory search — wider than the automatic per-prompt recall. Use when the user asks what you remember about something, says "/recall", "recall", "qu'est-ce que tu sais sur…", "tu te souviens de…", or when the automatic block came back empty and the question clearly depends on history.
---

# Deeper recall

Every prompt already gets an automatic top-3 above threshold. This skill is for
when that is not enough: a broad question, a search across a longer history, or
a deliberate "what do you actually know about X?".

## Run it

```bash
bin/memory search "<query>" -k 10 -v
```

The query should be the **user's question rephrased as a statement of the
topic**, not their literal words — embeddings match meaning, and a bare "tu te
souviens ?" carries none. French and English work equally well against
memories in either language, so do not translate the query either.

Useful variations:

```bash
bin/memory search "<query>" -k 10 -v --threshold 0.35   # nothing came back at the default
bin/memory search "<query>" --scope shared -v           # only what every persona knows
bin/memory search "<query>" --scope <you> -v            # only what is yours
bin/memory list --scope <you>                           # everything, newest first
bin/memory show <id>                                    # one memory in full, plus how often it was recalled
```

Lowering `--threshold` is the right move when a search comes back empty and you
believe something exists. Say what you found and how sure you are, rather than
silently presenting a weak match as a firm recollection.

## Report it as memory, not as search results

Answer in prose, in the user's language, as someone recalling. Weave the
content in; do not paste the table of scores and ids. Distinguish what you
remember clearly (high scores, specific bodies) from what you half-remember
(borderline scores) — "je crois me souvenir que…" is honest and useful.

If nothing comes back even at a lowered threshold, say so plainly: you do not
remember. Do not reconstruct it from context and present it as memory.

## If it surfaces a problem

- **Two memories saying the same thing** → offer `bin/memory supersede <old> <new>`.
- **A memory that is now wrong** → offer to store the corrected version and
  supersede the old one (via the `memory-store` skill — still needs a yes).
- **A lot of noise** → mention `/memory-compact`.
