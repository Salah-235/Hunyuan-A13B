"""Hybrid retrieval: BM25 keyword search (SQLite FTS5) + optional dense embeddings,
fused with Reciprocal Rank Fusion."""
import sqlite3
import threading

import numpy as np

from .textproc import fts_query

RRF_K = 60


class VectorIndex:
    """All chunk embeddings in one normalised matrix, rebuilt lazily after changes."""

    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.dirty = True
        self.ids = np.zeros(0, dtype=np.int64)
        self.doc_ids = []
        self.matrix = None

    def invalidate(self):
        self.dirty = True

    def _load(self):
        rows = self.db.query("SELECT chunk_id, doc_id, vector FROM embeddings ORDER BY chunk_id")
        if not rows:
            self.ids, self.doc_ids, self.matrix = np.zeros(0, dtype=np.int64), [], None
        else:
            self.ids = np.array([r["chunk_id"] for r in rows], dtype=np.int64)
            self.doc_ids = [r["doc_id"] for r in rows]
            self.matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        self.dirty = False

    def search(self, query_vector, limit, doc_filter=None):
        with self.lock:
            if self.dirty:
                self._load()
            if self.matrix is None:
                return []
            q = np.asarray(query_vector, dtype=np.float32)
            norm = np.linalg.norm(q)
            if norm == 0 or q.shape[0] != self.matrix.shape[1]:
                return []
            scores = self.matrix @ (q / norm)
            if doc_filter is not None:
                mask = np.array([d in doc_filter for d in self.doc_ids])
                scores = np.where(mask, scores, -np.inf)
            order = np.argsort(-scores)[:limit]
            return [(int(self.ids[i]), float(scores[i])) for i in order if np.isfinite(scores[i])]


def to_blob(vector):
    v = np.asarray(vector, dtype=np.float32)
    n = np.linalg.norm(v)
    return (v / n if n else v).astype(np.float32).tobytes()


class Retriever:
    def __init__(self, db, llm):
        self.db = db
        self.llm = llm
        self.vectors = VectorIndex(db)

    def keyword_search(self, query, limit, doc_filter=None):
        match = fts_query(query)
        if not match:
            return []
        sql = "SELECT chunk_id, doc_id, bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ?"
        params = [match]
        if doc_filter is not None:
            if not doc_filter:
                return []
            sql += " AND doc_id IN (%s)" % ",".join("?" * len(doc_filter))
            params.extend(doc_filter)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        try:
            rows = self.db.query(sql, params)
        except sqlite3.OperationalError:
            return []
        return [(int(r["chunk_id"]), -float(r["score"])) for r in rows]

    def dense_search(self, query, limit, doc_filter=None):
        if not self.llm.embeddings_enabled:
            return []
        vector = self.llm.embed([query])[0]
        return self.vectors.search(vector, limit, set(doc_filter) if doc_filter is not None else None)

    def search(self, queries, top_k, doc_filter=None):
        """Run every query through both retrievers and fuse all rankings (RRF)."""
        fused = {}
        pool = max(top_k * 4, 30)
        for query in queries:
            rankings = [self.keyword_search(query, pool, doc_filter)]
            try:
                rankings.append(self.dense_search(query, pool, doc_filter))
            except Exception:  # noqa: BLE001 - embeddings server down -> keyword search still works
                pass
            for ranking in rankings:
                for rank, (chunk_id, _score) in enumerate(ranking):
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]
        if not best:
            return []
        ids = [chunk_id for chunk_id, _ in best]
        rows = self.db.query(
            "SELECT c.id, c.doc_id, c.idx, c.page_start, c.page_end, c.heading, c.text, c.ocr, "
            "d.title, d.ext, d.category FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE c.id IN (%s) AND d.status = 'ready'" % ",".join("?" * len(ids)),
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        return [dict(by_id[i], score=s) for i, s in best if i in by_id]
