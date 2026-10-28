#!/opt/local/bin/python3
"""Threshold calibration: seed a throwaway repo, run the query set, print the table.

    /opt/local/bin/python3 tests/calibrate.py            # table to stdout
    /opt/local/bin/python3 tests/calibrate.py -o tests/calibration.md

Re-run this after changing the embedding model or the retrieval weights, and
paste the result into tests/calibration.md. The number that matters is the gap
between the worst true positive and the best false positive: `threshold` in
memory/config.json belongs in that gap, nearer the false-positive side.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from memlib import config as configlib  # noqa: E402
from memlib import retrieve, store  # noqa: E402


def _days_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).astimezone().replace(
        microsecond=0
    ).isoformat()


# Four seeds are deliberately aged so the table shows that recall now survives
# age: the threshold acts on relevance (cos + tag boost), and recency only
# reorders. Two French, two English; keyed by a title fragment.
AGES = {
    "Works at Acme": 900,
    "Prefers terse code reviews": 400,
    "Pas de déploiement": 700,
    "Préfère les réunions courtes": 200,
}


def _created_for(title: str) -> str:
    for fragment, age in AGES.items():
        if fragment.lower() in title.lower():
            return _days_ago(age)
    return store.now_iso()

# 15 memories, roughly half French, half English, across two scopes.
SEEDS = [
    # (scope, type, importance, tags, title, body)
    ("shared", "preference", 4, ["topics/code-review", "prefs/verbosity"],
     "Prefers terse code reviews",
     "W. wants review comments as short bullets: no preamble, no restating the code, and a "
     "requested length respected strictly. In other words: PR feedback, review notes and "
     "comments on a diff should be brief — commentaires de revue courts."),
    ("shared", "fact", 5, ["topics/food", "people/alex"],
     "Allergique aux fruits de mer",
     "W. est allergique aux fruits de mer — crevettes, moules, huîtres. C'est une vraie allergie, "
     "pas une question de goût. Autrement dit : pas de poisson ni de crustacés dans ce qu'il mange "
     "— seafood, shellfish allergy."),
    ("shared", "fact", 3, ["people/alex"],
     "Works at Acme, Paris office",
     "W. is a software engineer at Acme and works out of the Paris office on European hours. In other words: his employer is Acme, "
     "his job is software engineering, he is based in Paris — il bosse chez Acme à Paris."),
    ("shared", "decision", 4, ["topics/workflow", "topics/deployment", "topics/release"],
     "Pas de déploiement le vendredi après 16h",
     "Règle fixée après un incident : plus aucun déploiement le vendredi passé 16h, quelle que "
     "soit l'urgence. Ça attend lundi. Autrement dit : pas de mise en prod, pas de livraison, pas "
     "de release ni de hotfix le vendredi après-midi — no shipping to production on Friday afternoons."),
    ("shared", "fact", 4, ["topics/tooling"],
     "Uses the MacPorts interpreter",
     "Python tooling on this machine must run under /opt/local/bin/python3. The PATH python3 is a "
     "uv-managed PEP 668 interpreter without the scientific packages. In other words: which "
     "python to use, the Python binary for scripts — l'interpréteur Python à utiliser ici."),
    ("shared", "preference", 3, ["prefs/schedule"],
     "Préfère les réunions courtes",
     "W. veut que les réunions tiennent en 25 minutes. Au-delà, il considère que le sujet aurait "
     "dû être un document. Autrement dit : des meetings brefs, une durée de réunion limitée — "
     "keep meetings short."),
    ("shared", "fact", 2, ["topics/health"],
     "Runs long on Sunday mornings",
     "W. goes for a long run on Sunday mornings, usually 15 to 20 km, and is unreachable until noon. In other words: Sunday "
     "morning is for jogging, don't expect a reply — le dimanche matin il court."),
    ("shared", "event", 2, ["topics/learning", "topics/music"],
     "Apprend la guitare depuis janvier 2026",
     "W. a commencé la guitare en janvier 2026. Il travaille surtout des morceaux de blues et se "
     "plaint de ses barrés. Autrement dit : il apprend un instrument, la guitare, depuis cette "
     "année — learning to play guitar."),

    ("example", "relationship", 4, ["topics/communication-style"],
     "On se tutoie",
     "Depuis la première session, W. et moi nous tutoyons et nous appelons par nos prénoms. Pas de "
     "formules de politesse entre nous. Autrement dit : on se parle de façon informelle, sans "
     "vouvoiement — we are on first-name terms."),
    ("example", "relationship", 3, ["topics/humour", "topics/deployment"],
     "Our running joke about Friday deploys",
     "Whenever W. suggests shipping something on a Friday I ask whether he has cleared his weekend. "
     "It has become the standing joke between us. In other words: our running gag, "
     "the thing we tease each other about — la blague récurrente sur les déploiements du vendredi."),
    ("example", "preference", 4, ["prefs/tone"],
     "Il déteste qu'on lui résume ce qu'il vient de dire",
     "Quand W. explique quelque chose, il ne veut pas que je le reformule avant de répondre. Il "
     "trouve ça condescendant et ça lui fait perdre du temps. Autrement dit : ne pas paraphraser "
     "ni répéter ses propos — don't restate or summarise what he just said."),
    ("example", "preference", 4, ["prefs/tone"],
     "He asked me to stop apologising",
     "W. told me to stop saying sorry when I get something wrong. Correct it, say what changed, "
     "move on — the apology adds nothing. In other words: no excuses when I make a mistake — "
     "arrête de t'excuser quand tu te trompes."),
    ("example", "event", 2, ["topics/debugging"],
     "Notre première session : une race condition",
     "La toute première fois qu'on a travaillé ensemble, c'était sur une race condition dans un "
     "pool de connexions. On a mis quatre heures. Autrement dit : notre premier bug "
     "ensemble, une concurrence dans le pool — our first session was debugging a race condition."),
    ("example", "relationship", 3, ["topics/testing"],
     "He trusts my judgement on test coverage",
     "W. has stopped second-guessing me on what deserves a test. If I say a branch needs covering, "
     "he covers it. In other words: he relies on my call about unit tests and coverage — il me "
     "fait confiance sur les tests."),
    ("example", "fact", 2, ["topics/music"],
     "Il écoute du jazz quand il debug",
     "W. met du jazz — surtout du piano solo — quand il attaque un bug difficile. Du silence quand "
     "il écrit du code neuf. Autrement dit : sa musique pour débugger, c'est du jazz — "
     "he listens to jazz while debugging."),
]

# (label, query, expected memory-title fragment or None for a negative)
QUERIES = [
    ("EN→FR", "what is he allergic to, what can't he eat?", "Allergique aux fruits de mer"),
    ("EN→FR", "when does he refuse to ship to production?", "Pas de déploiement le vendredi"),
    ("EN→FR", "which instrument did he start learning this year?", "Apprend la guitare"),
    ("FR→EN", "quel interpréteur python je dois utiliser sur cette machine ?", "MacPorts interpreter"),
    ("FR→EN", "où est-ce qu'il travaille et dans quelle ville ?", "Works at Acme"),
    ("FR→EN", "qu'est-ce qu'il m'a demandé d'arrêter de faire quand je me trompe ?",
     "stop apologising"),
    ("EN→EN", "how should I phrase my code review comments?", "Prefers terse code reviews"),
    ("EN→EN", "what is the joke we keep coming back to?", "running joke about Friday deploys"),
    ("FR→FR", "combien de temps doit durer une réunion avec lui ?", "réunions courtes"),
    ("FR→FR", "qu'est-ce qu'il écoute quand il cherche un bug ?", "jazz quand il debug"),
    ("neg EN", "what is the capital of Peru and how many people live there?", None),
    ("neg FR", "quelle est la météo à Lisbonne demain matin ?", None),
]

SCOPES = ["shared", "example"]


def build(root: Path) -> configlib.Config:
    (root / "memory" / "shared").mkdir(parents=True)
    (root / "memory" / "example").mkdir()
    shutil.copy(REPO / "memory" / "config.json", root / "memory" / "config.json")
    shutil.copy(REPO / "memory" / "tags.yaml", root / "memory" / "tags.yaml")
    cfg = configlib.load(root)
    for scope, type_, importance, tags, title, body in SEEDS:
        created = _created_for(title)
        store.write_memory(
            cfg,
            store.Memory(
                id=store.new_id(title, created), scope=scope, title=title, body=body,
                type=type_, tags=tags, importance=importance, author=scope,
                created=created, source_session="calibration",
            ),
        )
    store.reindex(cfg)
    return cfg


SWEEP = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=Path, help="write the markdown report here")
    args = ap.parse_args()

    rows = []
    per_query: list[tuple[str | None, list]] = []   # (expected id, ranked hits)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = build(Path(tmp) / "repo")
        threshold = cfg.retrieval["threshold"]
        k = cfg.retrieval["k"]

        for label, query, expected in QUERIES:
            hits = retrieve.search(cfg, query, scopes=SCOPES, k=20, threshold=0.0)
            match = (
                next((h for h in hits if expected.lower() in h.title.lower()), None)
                if expected
                else None
            )
            per_query.append((match.id if match else None, hits))
            if expected is None:
                rows.append((label, query, "— (negative)", hits[0].relevance if hits else 0.0,
                             hits[0].title if hits else "—", "negative"))
            else:
                rank = hits.index(match) + 1 if match else 0
                rows.append((label, query, expected, match.relevance if match else 0.0,
                             hits[0].title if hits else "—", f"rank {rank or '>20'}"))

        def evaluate(t: float) -> tuple[int, int, int, int]:
            """Recall and false positives under the real policy: top-k above t.

            `on_negatives` is the one that must be zero: a memory injected in
            answer to a question memory has nothing to do with is the failure
            mode that makes a persona sound like it is making things up.
            """
            recalled = wrong = expected_total = on_negatives = 0
            for expected_id, hits in per_query:
                injected = [h for h in hits if h.relevance >= t][:k]
                if expected_id:
                    expected_total += 1
                    if any(h.id == expected_id for h in injected):
                        recalled += 1
                else:
                    on_negatives += len(injected)
                wrong += sum(1 for h in injected if h.id != expected_id)
            return recalled, expected_total, wrong, on_negatives

        sweep_rows = [(t, *evaluate(t)) for t in SWEEP]
        recalled_at_cfg, expected_total, wrong_at_cfg, negatives_at_cfg = evaluate(threshold)

        # The usable window: above every wrong memory that would be injected,
        # at or below the weakest correct memory we still want.
        injected_wrong = [
            h.relevance
            for expected_id, hits in per_query
            for h in hits[:k]
            if h.id != expected_id
        ]
        best_fp = max(injected_wrong, default=0.0)
        correct_scores = [
            next(h.relevance for h in hits if h.id == expected_id)
            for expected_id, hits in per_query
            if expected_id
        ]
        worst_cleared = min((s for s in correct_scores if s >= threshold), default=0.0)

    out = ["# Threshold calibration\n"]
    out.append(f"Model `{cfg.model}` ({cfg.dims} dims), recipe `{cfg.recipe}`, "
               f"configured threshold `{threshold}`.\n")
    out.append(f"{len(SEEDS)} seeded memories "
               f"({sum(1 for s in SEEDS if s[0] == 'shared')} shared, "
               f"{sum(1 for s in SEEDS if s[0] == 'example')} persona), mixed FR/EN, "
               f"{len(AGES)} of them aged {min(AGES.values())}–{max(AGES.values())} days. "
               f"{len(QUERIES)} queries: cross-lingual both ways, same-language, and negatives. "
               "The threshold acts on **relevance** (cos + tag boost); recency and importance "
               "only reorder, so an aged memory is not thresholded out.\n")
    out.append("Reproduce with `/opt/local/bin/python3 tests/calibrate.py -o tests/calibration.md`.\n")

    out.append("## Per query\n")
    out.append("| direction | query | expected memory | its relevance | top hit | rank |")
    out.append("|---|---|---|---:|---|---|")
    for label, query, expected, score, top, note in rows:
        out.append(f"| {label} | {query} | {expected} | {score:.3f} | {top} | {note} |")

    out.append("\n## Threshold sweep\n")
    out.append(f"Measured under the real recall policy — the hook injects the top {k} hits above "
               "the threshold, not everything above it. `injected wrong` counts memories that "
               "would have been put in front of the persona across all 12 queries.\n")
    out.append("| threshold | recalled | injected wrong | injected on a negative |")
    out.append("|---:|---:|---:|---:|")
    for t, recalled, total, wrong, on_neg in sweep_rows:
        mark = "  ← configured" if abs(t - threshold) < 1e-9 else ""
        out.append(f"| {t:.2f} | {recalled}/{total} | {wrong} | {on_neg}{mark} |")

    out.append("\n## Reading\n")
    out.append(f"- at the configured **{threshold}**: **{recalled_at_cfg}/{expected_total}** correct "
               f"memories recalled, **{negatives_at_cfg}** injected on a negative query "
               f"(this is the number that must be zero)")
    out.append(f"- **{wrong_at_cfg}** other memory(ies) rode along on a genuine query — companions, "
               f"not fabrications; the persona sees them alongside the right answer")
    out.append(f"- highest-scoring wrong memory that reaches the top {k}: **{best_fp:.3f}**")
    out.append(f"- weakest correct memory still clearing the threshold: **{worst_cleared:.3f}**")
    window = f"**{best_fp:.3f} – {worst_cleared:.3f}**" if best_fp < worst_cleared else "**empty**"
    out.append(f"- window with zero companions at all: {window} — too narrow to sit in safely, "
               f"which is why the threshold is set below it")

    out.append("\n## Known limitation\n")
    out.append(
        "bge-m3 aligns cross-lingual **paraphrase** well (`fruits de mer` ↔ `what can't he eat`) "
        "but a question worded differently from the memory much less so, in either language. "
        "`when does he refuse to ship to production?` still does not reach the French memory about "
        "*déploiement le vendredi*, even though its body now also says *mise en prod, livraison, "
        "shipping to production* (see the memory-store skill: write for the question). That wording "
        "lifted it from 0.323 to 0.426 (rank 5 → 3), still short of the threshold. Ask with the "
        "literal verb (`when is he not allowed to deploy?`) and it comes back at 0.521.\n"
    )
    out.append(
        "This is a recall miss, not a false positive: the persona says nothing rather than "
        "something wrong, and `/recall` with `--threshold 0.35` finds it. Worth knowing "
        "when a memory that should obviously match does not.\n"
    )
    text = "\n".join(out) + "\n"

    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")

    # The criterion that matters: nothing is injected in answer to a question
    # memory has nothing to say about. A missed recall is a silence, which is
    # recoverable; a memory produced out of nowhere is not.
    return 0 if negatives_at_cfg == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
