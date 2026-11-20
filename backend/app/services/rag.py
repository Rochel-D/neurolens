"""RAG knowledge base — deep module.

Public interface:  retrieve(query, k=3, module=None) -> list[Doc]

The corpus is the curated knowledge notes (neuro conditions, general conditions) plus one
document per lab test built from lab_tests.json. Retrieval is TF-IDF cosine similarity
(scikit-learn): offline, deterministic and free. If sentence-transformers is installed and
RAG_EMBEDDINGS=1, dense embeddings are used instead.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from . import kb, lab_parser

DATA = Path(__file__).resolve().parents[2] / "data" / "knowledge.json"
MEDLINEPLUS_SEARCH = "https://vsearch.nlm.nih.gov/vivisimo/cgi-bin/query-meta?v%3Aproject=medlineplus&query="


@dataclass
class Doc:
    id: str
    title: str
    text: str
    module: str
    url: str
    score: float = 0.0

    def cite(self) -> dict:
        return {"id": self.id, "title": self.title, "url": self.url, "score": round(self.score, 3)}


def _learn_more(title: str) -> str:
    return MEDLINEPLUS_SEARCH + quote_plus(title)


@lru_cache
def corpus() -> list[Doc]:
    docs = [
        Doc(d["id"], d["title"], d["text"], d["module"], _learn_more(d["title"]))
        for d in kb.notes()
    ]
    for t in lab_parser.catalogue():
        text = f"{t['name']} ({', '.join(t['aliases'][:3])}). {t['what']} If low: {t['low_means']} If high: {t['high_means']}"
        docs.append(Doc(f"lab:{t['id']}", f"{t['name']} test", text, "general", _learn_more(t["name"] + " test")))
    return docs


class _Index:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        texts = [f"{d.title}. {d.title}. {d.text}" for d in docs]
        self.dense = False
        if os.getenv("RAG_EMBEDDINGS") == "1":
            try:
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
                self.matrix = self.model.encode(texts, normalize_embeddings=True)
                self.dense = True
                return
            except ImportError:
                pass
        from sklearn.feature_extraction.text import TfidfVectorizer

        # word n-grams for exact terms + character n-grams so "meninges" still finds "meningioma"
        self.vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")
        self.matrix = self.vec.fit_transform(texts)
        self.cvec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.cmatrix = self.cvec.fit_transform(texts)

    def search(self, query: str) -> list[float]:
        if self.dense:
            q = self.model.encode([query], normalize_embeddings=True)
            return (self.matrix @ q[0]).tolist()
        from sklearn.metrics.pairwise import cosine_similarity

        w = cosine_similarity(self.vec.transform([query]), self.matrix)[0]
        c = cosine_similarity(self.cvec.transform([query]), self.cmatrix)[0]
        return (0.6 * w + 0.4 * c).tolist()


@lru_cache
def _index() -> _Index:
    return _Index(corpus())


def retrieve(query: str, k: int = 3, module: str | None = None, min_score: float = 0.05) -> list[Doc]:
    idx = _index()
    scores = idx.search(query)
    ranked = sorted(zip(idx.docs, scores), key=lambda p: -p[1])
    out = []
    for d, s in ranked:
        if module and d.module != module:
            continue
        if s < min_score:
            break
        out.append(Doc(d.id, d.title, d.text, d.module, d.url, float(s)))
        if len(out) == k:
            break
    return out


def get(doc_id: str) -> Doc | None:
    return next((d for d in corpus() if d.id == doc_id), None)


def _clear() -> None:
    corpus.cache_clear()
    _index.cache_clear()


kb.on_change(_clear)
