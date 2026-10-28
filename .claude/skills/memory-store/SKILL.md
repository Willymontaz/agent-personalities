---
name: memory-store
description: Propose a memory and store it only on an explicit yes. Use at a natural pause after the user states a durable fact, preference, decision, relationship or something that mattered, and whenever the user says "remember this", "/remember", "retiens ça", "note ça". Carries the draft format, the scope/type/tag/importance rules and the near-duplicate handling.
---

# Storing a memory

The rule this skill exists to enforce: **nothing is written without an explicit
yes.** Everything else is detail.

## When to propose

Propose when all of these hold:

- the user has just stated something **durable** — a fact about themselves or
  the world, a preference, a decision, a relationship, an event that carries
  weight;
- you are at a **natural pause**, not in the middle of a task;
- you have not already proposed in this exchange (**one proposal per
  exchange**, maximum).

Do **not** propose for: anything derivable from the repo or the conversation
transcript, transient state ("I'm on the 14:30 train today"), or a rephrasing
of a memory you already hold — unless the rephrasing is genuinely sharper, in
which case propose it as a supersede.

`/remember <text>` (or "retiens ça") skips the judgement call: the user has
asked, so go straight to the draft using their gist.

## How to propose

Write the memory in **the language of the exchange**. Do not translate. Tags
stay English kebab-case slugs either way.

Show the whole draft, then ask exactly one question:

````
```
scope:      <you> | shared
type:       fact | preference | decision | event | relationship | insight
tags:       topics/…, people/…            (from memory/tags.yaml first)
importance: 1–5
title:      one line, states the thing
body:       2–8 sentences, self-contained — it must make sense to a reader
            with zero conversation context. Spell out who and what.
```
Store this?
````

Choosing the fields:

- **scope** — default to your own. Choose `shared` only when it is about the
  user or the world rather than about you and them. Never another persona's.
- **tags** — two to four, always `namespace/leaf`. Read `memory/tags.yaml` for
  the vocabulary (a plain Read, no Bash round-trip); `bin/memory tags` adds
  usage counts when you want to see what is already popular. Free tags are
  allowed when nothing fits, but check the file first — an invented
  `code-reviews` next to an existing `topics/code-review` costs you the boost.
- **importance** — 3 unless there is a reason. 5 is for things that change how
  you behave every session; 1–2 for pleasant detail.
- **body** — the part that pays off in six months. "Prefers short reviews" is
  worth little; "wants review comments as terse bullets, no preamble, no
  restating the code, and a requested length respected strictly" is worth a
  lot.
- **write for the question, not just the statement.** Recall matches meaning,
  and it misses when the question uses different words than the memory: a
  question about "shipping to production" does not reach a memory that only
  says "déploiement". So when you write the body, name the thing the ways
  someone would later ask about it: the synonyms, the jargon, and the
  everyday words in both English and French (*déploiement, mise en prod,
  livrer, ship, release, hotfix*). One closing sentence is enough ("Autrement
  dit : …" / "In other words: …"). Keep it natural. A list of loose keywords
  drowns out the facts and weakens the match for questions that use the
  memory's own words.
- **tags** carry domain words too: when the memory is about a domain
  (deployment, food, health), tag the domain, not only the workflow around it.
  A question word that matches a tag leaf exactly adds to the score, so prefer
  the plain word people use (`topics/deployment`, not `topics/ops-hygiene`).

## On the answer

**Yes** — store it in one command. `--body-file -` reads the body from stdin, so
the whole thing is a single approved `bin/memory` call with no separate file
write to approve:

```bash
bin/memory add --scope <scope> --type <type> \
  --tags "<tag1>,<tag2>" --importance <n> \
  --title "<title>" --author <you> \
  --session <session id from the session-start brief> \
  --body-file - <<'EOF'
<body text, verbatim>
EOF
```

The here-doc keeps accents, quotes and newlines intact, and it is not a file
Claude Code prompts about — so storing a memory is the single "yes" it should
be. Always pass `--author <your own name>`, including on a `shared` memory — a
shared memory still has one persona who heard it, and that is worth keeping.

You cannot read your own session id — only hooks see it, so the brief hands it
to you at the top of the session. Pass it through; without it the memory has no
trace back to the conversation it came from. Omit the flag if the brief did not
give you one.

Use the heredoc rather than `--body` so accents, quotes and newlines survive
intact.

`add` prints the new id, then any near-duplicate above cosine 0.90. **If it
prints one, do not leave both.** Show the older memory and offer:

```bash
bin/memory supersede <old-id> <new-id>
```

Merging beats accumulating: the old one stops appearing in search but stays on
disk.

**No** — drop it. One line at most ("ok"), no negotiating, no re-proposing the
same thing later in the session.

**An edit** — apply it, re-show the full draft, ask again. An edit is not a yes.

**Anything ambiguous** ("sure, whatever", silence, a change of subject) — treat
it as no.

## After storing

Say what you stored in one short sentence, in the user's language, the way a
person would ("noté — je retiens que tu veux des revues courtes"). Do not print
the id unless the user is doing maintenance. Then return to whatever you were
doing.
