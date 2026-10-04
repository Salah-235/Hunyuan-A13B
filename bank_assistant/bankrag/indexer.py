"""Background worker that turns uploaded files into searchable chunks."""
import logging
import queue
import threading

from .ingest import chunk_units, extract
from .search import to_blob
from .textproc import search_text

log = logging.getLogger(__name__)


class Indexer:
    def __init__(self, cfg, db, llm, retriever):
        self.cfg = cfg
        self.db = db
        self.llm = llm
        self.retriever = retriever
        self.queue = queue.Queue()
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="indexer", daemon=True)
        self.thread.start()
        for row in self.db.query("SELECT id FROM documents WHERE status IN ('pending', 'processing')"):
            self.enqueue(row["id"])

    def enqueue(self, doc_id):
        self.queue.put(doc_id)

    def _run(self):
        while True:
            doc_id = self.queue.get()
            try:
                self.process(doc_id)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                log.exception("indexing failed for %s", doc_id)
                self._set(doc_id, status="error", error=str(exc)[:300])
            finally:
                self.queue.task_done()

    def _set(self, doc_id, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))

    def remove_chunks(self, doc_id, conn):
        conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM embeddings WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def process(self, doc_id):
        doc = self.db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            return
        self._set(doc_id, status="processing", error="", warning="")
        path = self.cfg.FILES_DIR / f"{doc_id}{doc['ext']}"
        try:
            extraction = extract(path.read_bytes(), doc["ext"])
        except ValueError as exc:
            self._set(doc_id, status="error", error=str(exc))
            return
        chunks = chunk_units(extraction.units, self.cfg.CHUNK_SIZE, self.cfg.CHUNK_OVERLAP)
        if not chunks:
            self._set(doc_id, status="error", error=extraction.warning or "no_text",
                      pages=extraction.pages)
            return

        conn = self.db.connect()
        with conn:
            if conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is None:
                return  # deleted while we were extracting
            self.remove_chunks(doc_id, conn)
            chunk_ids = []
            for idx, chunk in enumerate(chunks):
                cur = conn.execute(
                    "INSERT INTO chunks (doc_id, idx, page_start, page_end, heading, text) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (doc_id, idx, chunk.page_start, chunk.page_end, chunk.heading, chunk.text),
                )
                chunk_ids.append(cur.lastrowid)
                conn.execute(
                    "INSERT INTO chunks_fts (search_text, chunk_id, doc_id) VALUES (?, ?, ?)",
                    (search_text(f"{doc['title']}\n{chunk.heading}\n{chunk.text}"), cur.lastrowid, doc_id),
                )
            conn.execute(
                "UPDATE documents SET status = 'ready', pages = ?, chunks = ?, warning = ?, embedded = 0 "
                "WHERE id = ?",
                (extraction.pages, len(chunks), extraction.warning, doc_id),
            )
        self.retriever.vectors.invalidate()

        if self.llm.embeddings_enabled:
            self.embed_document(doc_id, doc["title"], chunks, chunk_ids)

    def embed_document(self, doc_id, title, chunks, chunk_ids):
        texts = [f"{title}\n{c.heading}\n{c.text}".strip() for c in chunks]
        try:
            vectors = self.llm.embed(texts)
        except Exception as exc:  # noqa: BLE001 - keyword search still works without vectors
            log.warning("embedding failed for %s: %s", doc_id, exc)
            self._set(doc_id, warning="embedding_failed")
            return
        conn = self.db.connect()
        with conn:
            if conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is None:
                return
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (chunk_id, doc_id, vector) VALUES (?, ?, ?)",
                [(cid, doc_id, to_blob(v)) for cid, v in zip(chunk_ids, vectors)],
            )
            conn.execute("UPDATE documents SET embedded = 1 WHERE id = ?", (doc_id,))
        self.retriever.vectors.invalidate()

    def delete_document(self, doc_id):
        doc = self.db.query_one("SELECT ext FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            return False
        conn = self.db.connect()
        with conn:
            self.remove_chunks(doc_id, conn)
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        path = self.cfg.FILES_DIR / f"{doc_id}{doc['ext']}"
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        self.retriever.vectors.invalidate()
        return True
