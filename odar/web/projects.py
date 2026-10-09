"""Project files: chunking and local BM25 retrieval (no network, no model).

Embeddings are deliberately not used: BM25 over ~900-character chunks is
fast, deterministic, and needs nothing beyond the standard library. Chunks
keep their file name so an answer can cite "file name + passage".
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Sequence

CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
MAX_FILES_PER_PROJECT = 20
MAX_CHUNKS_PER_FILE = 400

_TOKEN = re.compile(r"[\w\u0900-\u097f]+", re.UNICODE)
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "what which who why how when where does do did can could should would about into than then there their".split()
)


def tokens(text: str) -> List[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN.finditer(text or "")) if t not in _STOP and len(t) > 1]


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Paragraph-aware chunks of about ``size`` characters with a small overlap."""
    text = re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks: List[str] = []
    cur = ""
    for para in paras:
        while len(para) > size:  # very long paragraph: hard split on a sentence/space boundary
            cut = max(para.rfind(". ", 0, size), para.rfind(" ", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            piece, para = para[:cut].strip(), para[cut:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        if cur and len(cur) + len(para) + 1 > size:
            chunks.append(cur)
            cur = cur[-overlap:].split(" ", 1)[-1] if overlap else ""
        cur = f"{cur}\n{para}".strip() if cur else para
    if cur:
        chunks.append(cur)
    return chunks[:MAX_CHUNKS_PER_FILE]


def bm25_search(
    query: str, chunks: Sequence[Dict[str, Any]], k: int = 5, k1: float = 1.5, b: float = 0.75
) -> List[Dict[str, Any]]:
    """Rank ``chunks`` (dicts with a ``text`` key) for ``query``; returns the top ``k``
    with a ``score`` added. Chunks sharing no term with the query are dropped."""
    q = tokens(query)
    if not q or not chunks:
        return []
    docs = [tokens(c.get("text", "")) for c in chunks]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    scored = []
    for chunk, doc in zip(chunks, docs):
        tf = Counter(doc)
        score = 0.0
        for term in set(q):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf[term]
            score += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * len(doc) / avg))
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: -item[0])
    return [dict(chunk, score=round(score, 3)) for score, chunk in scored[:k]]
