"""Scoring and search over the memory index.

Score, in the order the plan fixes:

    1. cos          = query . memory            (both L2-normalised)
    2. tag boost    = +0.05 per query keyword matching a tag leaf, capped +0.10
    3. recency      = x (0.85 + 0.15 * exp(-age_days / 180))
    4. importance   = +0.02 * (importance - 3)

Brute-force numpy over the whole scope set; thousands of memories is nothing.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from .config import Config
from . import embedder, store

STOPWORDS = {
    # EN
    "the", "and", "for", "with", "that", "this", "what", "when", "how", "was", "are",
    "you", "your", "his", "her", "its", "our", "their", "about", "from", "into", "have",
    "has", "had", "can", "should", "would", "could", "does", "did", "not", "but", "any",
    # FR
    "les", "des", "une", "un", "le", "la", "du", "de", "dans", "pour", "avec", "que",
    "qui", "quoi", "comment", "est", "sont", "ete", "ton", "son", "ses", "mes", "nos",
    "leur", "sur", "par", "pas", "plus", "mais", "tout", "tous", "cette", "ces", "ils",
    "elle", "elles", "nous", "vous", "j'ai", "c'est", "il", "en", "au", "aux",
}


@dataclass
class Hit:
    id: str
    scope: str
    type: str
    title: str
    body: str
    tags: list[str]
    importance: int
    created: str
    cos: float
    relevance: float
    score: float

    def one_line(self) -> str:
        body = " ".join(self.body.split())
        return (
            f"- [{self.id}] ({self.scope}, {self.type}, tags: {', '.join(self.tags) or 'none'}) "
            f"{self.title} — {body}"
        )


def keywords(text: str) -> set[str]:
    words = re.findall(r"[\w'-]+", text.lower(), flags=re.UNICODE)
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def tag_boost(tags: list[str], kws: set[str], per_match: float, cap: float) -> float:
    """+per_match for each query keyword that hits a tag leaf (or a leaf part)."""
    leaf_parts: set[str] = set()
    for tag in tags:
        leaf = tag.split("/")[-1].lower()
        leaf_parts.add(leaf)
        leaf_parts.update(p for p in re.split(r"[-_.]", leaf) if len(p) >= 3)
    matched = kws & leaf_parts
    return min(cap, per_match * len(matched))


def score_rows(
    cfg: Config,
    rows: list[sqlite3.Row],
    mat: np.ndarray,
    query: str,
    qvec: np.ndarray,
) -> list[Hit]:
    r = cfg.retrieval
    kws = keywords(query)
    cosines = mat @ qvec
    hits: list[Hit] = []
    for row, cos in zip(rows, cosines):
        tags = store.tags_of(row)
        # relevance = what a memory is *about*; it decides the threshold.
        # score = relevance reweighted by recency and importance; it only orders.
        # Keeping them separate stops an old-but-relevant memory from silently
        # dropping below the threshold as it ages.
        relevance = float(cos) + tag_boost(tags, kws, r["tag_boost_per_match"], r["tag_boost_cap"])
        days = store.age_days(row["created"])
        s = relevance * (r["recency_base"] + r["recency_span"] * math.exp(-days / r["recency_scale_days"]))
        s += r["importance_weight"] * ((row["importance"] or 3) - 3)
        hits.append(
            Hit(
                id=row["id"],
                scope=row["scope"],
                type=row["type"] or "fact",
                title=row["title"] or "",
                body=row["body"] or "",
                tags=tags,
                importance=row["importance"] or 3,
                created=row["created"] or "",
                cos=float(cos),
                relevance=relevance,
                score=s,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


def search(
    cfg: Config,
    query: str,
    scopes: list[str] | None = None,
    k: int | None = None,
    threshold: float | None = None,
    conn: sqlite3.Connection | None = None,
    include_superseded: bool = False,
    exclude_ids: set[str] | None = None,
) -> list[Hit]:
    owned = conn is None
    conn = conn or store.connect(cfg)
    try:
        rows, mat = store.load_scope(conn, cfg, scopes, include_superseded)
        if not rows:
            return []
        qvec = embedder.embed_query(cfg, query)
        hits = score_rows(cfg, rows, mat, query, qvec)
    finally:
        if owned:
            conn.close()

    k = cfg.retrieval["k"] if k is None else k
    threshold = cfg.retrieval["threshold"] if threshold is None else threshold
    # Threshold on relevance (cos + tag boost), not the recency/importance-weighted
    # score: a memory is worth recalling because of what it is about, not its age.
    out = [h for h in hits if h.relevance >= threshold]
    if exclude_ids:
        out = [h for h in out if h.id not in exclude_ids]
    return out[:k]
