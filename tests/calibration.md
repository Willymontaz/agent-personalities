# Threshold calibration

Model `bge-m3` (1024 dims), recipe `v1`, configured threshold `0.5`.

15 seeded memories (8 shared, 7 persona), mixed FR/EN, 4 of them aged 200–900 days. 12 queries: cross-lingual both ways, same-language, and negatives. The threshold acts on **relevance** (cos + tag boost); recency and importance only reorder, so an aged memory is not thresholded out.

Reproduce with `/opt/local/bin/python3 tests/calibrate.py -o tests/calibration.md`.

## Per query

| direction | query | expected memory | its relevance | top hit | rank |
|---|---|---|---:|---|---|
| EN→FR | what is he allergic to, what can't he eat? | Allergique aux fruits de mer | 0.609 | Allergique aux fruits de mer | rank 1 |
| EN→FR | when does he refuse to ship to production? | Pas de déploiement le vendredi | 0.426 | Allergique aux fruits de mer | rank 3 |
| EN→FR | which instrument did he start learning this year? | Apprend la guitare | 0.734 | Apprend la guitare depuis janvier 2026 | rank 1 |
| FR→EN | quel interpréteur python je dois utiliser sur cette machine ? | MacPorts interpreter | 0.633 | Uses the MacPorts interpreter | rank 1 |
| FR→EN | où est-ce qu'il travaille et dans quelle ville ? | Works at Acme | 0.551 | Works at Acme, Paris office | rank 1 |
| FR→EN | qu'est-ce qu'il m'a demandé d'arrêter de faire quand je me trompe ? | stop apologising | 0.651 | He asked me to stop apologising | rank 1 |
| EN→EN | how should I phrase my code review comments? | Prefers terse code reviews | 0.758 | Prefers terse code reviews | rank 1 |
| EN→EN | what is the joke we keep coming back to? | running joke about Friday deploys | 0.539 | Our running joke about Friday deploys | rank 1 |
| FR→FR | combien de temps doit durer une réunion avec lui ? | réunions courtes | 0.578 | Préfère les réunions courtes | rank 1 |
| FR→FR | qu'est-ce qu'il écoute quand il cherche un bug ? | jazz quand il debug | 0.654 | Il écoute du jazz quand il debug | rank 1 |
| neg EN | what is the capital of Peru and how many people live there? | — (negative) | 0.201 | Uses the MacPorts interpreter | negative |
| neg FR | quelle est la météo à Lisbonne demain matin ? | — (negative) | 0.425 | Runs long on Sunday mornings | negative |

## Threshold sweep

Measured under the real recall policy — the hook injects the top 3 hits above the threshold, not everything above it. `injected wrong` counts memories that would have been put in front of the persona across all 12 queries.

| threshold | recalled | injected wrong | injected on a negative |
|---:|---:|---:|---:|
| 0.30 | 10/10 | 23 | 3 |
| 0.35 | 10/10 | 20 | 2 |
| 0.40 | 10/10 | 14 | 1 |
| 0.45 | 9/10 | 8 | 0 |
| 0.50 | 9/10 | 1 | 0  ← configured |
| 0.55 | 8/10 | 0 | 0 |
| 0.60 | 6/10 | 0 | 0 |

## Reading

- at the configured **0.5**: **9/10** correct memories recalled, **0** injected on a negative query (this is the number that must be zero)
- **1** other memory(ies) rode along on a genuine query — companions, not fabrications; the persona sees them alongside the right answer
- highest-scoring wrong memory that reaches the top 3: **0.516**
- weakest correct memory still clearing the threshold: **0.539**
- window with zero companions at all: **0.516 – 0.539** — too narrow to sit in safely, which is why the threshold is set below it

## Known limitation

bge-m3 aligns cross-lingual **paraphrase** well (`fruits de mer` ↔ `what can't he eat`) but a question worded differently from the memory much less so, in either language. `when does he refuse to ship to production?` still does not reach the French memory about *déploiement le vendredi*, even though its body now also says *mise en prod, livraison, shipping to production* (see the memory-store skill: write for the question). That wording lifted it from 0.323 to 0.426 (rank 5 → 3), still short of the threshold. Ask with the literal verb (`when is he not allowed to deploy?`) and it comes back at 0.521.

This is a recall miss, not a false positive: the persona says nothing rather than something wrong, and `/recall` with `--threshold 0.35` finds it. Worth knowing when a memory that should obviously match does not.

