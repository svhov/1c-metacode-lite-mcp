"""Embedding service — ONNX inference + sqlite-vec persistent vector store.

Runs entirely in-process, no external services needed.
Disabled by default — enable via ENABLE_EMBEDDING=true env var.
"""

import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ONNX Embedder (tokenizers + onnxruntime)
# ---------------------------------------------------------------------------

class OnnxEmbedder:
    """Generates embeddings using an ONNX-exported sentence-transformers model.

    Supports E5-style models with query/passage prefixes.
    """

    def __init__(self, model_path: str, query_prefix: str = "", passage_prefix: str = ""):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = os.path.join(model_path, "model.onnx")
        tokenizer_path = os.path.join(model_path, "tokenizer.json")

        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"ONNX model not found: {onnx_path}")
        if not os.path.exists(tokenizer_path):
            raise FileNotFoundError(f"Tokenizer not found: {tokenizer_path}")

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self._tokenizer.no_padding()  # we'll pad manually — encode_batch padding can panic
        self._tokenizer.no_truncation()  # we'll truncate manually too

        sess_opts = ort.SessionOptions()
        sess_opts.inter_op_num_threads = 1
        sess_opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(onnx_path, sess_opts,
                                              providers=["CPUExecutionProvider"])
        self._dimension = None
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        # Detect max token length: nomic=1024 (capped for stable RAM), e5=512
        self._max_length = 1024 if "nomic" in model_path.lower() else 512
        log.info("ONNX embedder loaded from %s (prefix=%s, max_tokens=%d)",
                 model_path, query_prefix.strip() or "none", self._max_length)

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            test = self.embed_single("test")
            self._dimension = len(test)
        return self._dimension

    def embed_single(self, text: str, is_query: bool = True) -> list[float]:
        prefix = self._query_prefix if is_query else self._passage_prefix
        return self.embed_batch([text], prefix=prefix)[0]

    def embed_passages(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Embed document/passage texts (with passage prefix for E5 models)."""
        return self.embed_batch(texts, batch_size=batch_size, prefix=self._passage_prefix)

    @staticmethod
    def _clean_text(text: str) -> str:
        """Whitelist-based text cleaning. Only allows known-safe character ranges.

        SentencePiece unigram models panic on unexpected Unicode. Instead of
        blacklisting bad chars (whack-a-mole), we whitelist safe ranges:
        - Basic Latin (U+0020–U+007E): ASCII printable
        - Latin Extended (U+00A0–U+024F): accented chars
        - Cyrillic (U+0400–U+04FF): Russian, Ukrainian, etc.
        - Common punctuation, digits, whitespace
        """
        cleaned = []
        for ch in text:
            cp = ord(ch)
            if ch in (' ', '\n', '\t', '\r'):
                cleaned.append(ch)
            elif 0x0020 <= cp <= 0x007E:   # ASCII printable
                cleaned.append(ch)
            elif 0x00A0 <= cp <= 0x024F:   # Latin Extended
                cleaned.append(ch)
            elif 0x0400 <= cp <= 0x04FF:   # Cyrillic
                cleaned.append(ch)
            elif 0x2000 <= cp <= 0x206F:   # General Punctuation (—, –, ", etc.)
                cleaned.append(ch)
            elif 0x2100 <= cp <= 0x214F:   # Letterlike Symbols (№, etc.)
                cleaned.append(ch)
            else:
                cleaned.append(' ')        # replace anything else with space
        return ''.join(cleaned)

    def _safe_encode(self, text: str) -> tuple[list[int], list[int]]:
        """Encode single text safely. Returns (ids, attention_mask), truncated to max_length."""
        cleaned = self._clean_text(text)
        ml = self._max_length
        try:
            enc = self._tokenizer.encode(cleaned)
            ids = list(enc.ids)[:ml]
            mask = list(enc.attention_mask)[:ml]
        except Exception:
            log.warning("Tokenization failed, using fallback for: %.40s...", text)
            ascii_text = cleaned.encode("ascii", errors="ignore").decode()
            if not ascii_text.strip():
                ascii_text = "empty"
            enc = self._tokenizer.encode(ascii_text)
            ids = list(enc.ids)[:ml]
            mask = list(enc.attention_mask)[:ml]
        return ids, mask

    def embed_batch(self, texts: list[str], batch_size: int = 64,
                    prefix: str = "") -> list[list[float]]:
        import numpy as np
        all_embeddings = []
        model_inputs = {inp.name for inp in self._session.get_inputs()}

        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]

            # Tokenize one-by-one (encode_batch panics on certain inputs)
            all_ids = []
            all_masks = []
            for text in chunk:
                ids, mask = self._safe_encode(prefix + text)
                all_ids.append(ids)
                all_masks.append(mask)

            # Pad to max length in batch
            max_len = max(len(ids) for ids in all_ids)
            if max_len == 0:
                max_len = 1
            for j in range(len(all_ids)):
                pad = max_len - len(all_ids[j])
                if pad > 0:
                    all_ids[j].extend([0] * pad)
                    all_masks[j].extend([0] * pad)

            input_ids = np.array(all_ids, dtype=np.int64)
            attention_mask = np.array(all_masks, dtype=np.int64)

            feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
            if "token_type_ids" in model_inputs:
                feeds["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)
            feeds = {k: v for k, v in feeds.items() if k in model_inputs}

            outputs = self._session.run(None, feeds)
            token_embeddings = outputs[0]

            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            summed = (token_embeddings * mask_expanded).sum(axis=1)
            counts = mask_expanded.sum(axis=1).clip(min=1e-9)
            mean_pooled = summed / counts

            norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True).clip(min=1e-9)
            normalized = mean_pooled / norms
            all_embeddings.extend(normalized.tolist())

        return all_embeddings


# ---------------------------------------------------------------------------
# Cross-encoder reranker
# ---------------------------------------------------------------------------

class CrossEncoderReranker:
    """Reranks results using a cross-encoder model for more precise scoring."""

    def __init__(self, model_path: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = os.path.join(model_path, "model.onnx")
        tokenizer_path = os.path.join(model_path, "tokenizer.json")

        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"Cross-encoder not found: {onnx_path}")

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

        sess_opts = ort.SessionOptions()
        sess_opts.inter_op_num_threads = 1
        sess_opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(onnx_path, sess_opts,
                                              providers=["CPUExecutionProvider"])
        log.info("Cross-encoder loaded from %s", model_path)

    def rerank(self, query: str, results: list[dict],
               text_key: str = "name", top_k: int = 7) -> list[dict]:
        """Rerank results by cross-encoder score. Returns top_k."""
        import numpy as np

        if not results:
            return results

        # Build pairs: (query, document_text)
        doc_texts = []
        for r in results:
            parts = [r.get(text_key, "")]
            if r.get("description"):
                parts.append(r["description"])
            if r.get("signature"):
                parts.append(r["signature"])
            if r.get("synonym"):
                parts.append(r["synonym"])
            if r.get("category"):
                parts.append(r["category"])
            doc_texts.append(" ".join(p for p in parts if p))

        # Tokenize pairs
        encoded = self._tokenizer.encode_batch(
            [(query, doc) for doc in doc_texts]
        )

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        model_inputs = {inp.name for inp in self._session.get_inputs()}
        feeds = {k: v for k, v in feeds.items() if k in model_inputs}

        outputs = self._session.run(None, feeds)
        # Cross-encoder outputs logits — higher = more relevant
        logits = outputs[0].flatten()

        # Sigmoid to normalize to 0..1
        scores = 1.0 / (1.0 + np.exp(-logits))

        # Attach scores and sort
        for r, score in zip(results, scores):
            r["score"] = round(float(score), 4)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


# ---------------------------------------------------------------------------
# sqlite-vec persistent vector store
# ---------------------------------------------------------------------------

def _serialize_f32(vec: list[float]) -> bytes:
    """Serialize float list to little-endian float32 bytes for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


class SqliteVecStore:
    """Persistent vector store using sqlite-vec extension."""

    def __init__(self, db_path: str, dimension: int = 384):
        self._db_path = db_path
        self._dimension = dimension
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")

        # Load sqlite-vec extension
        import sqlite_vec
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        self._create_tables()
        log.info("SqliteVecStore opened: %s (dim=%d)", db_path, dimension)

    def _create_tables(self):
        with self._lock:
            self._conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS vec_items (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    collection TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    synonym TEXT DEFAULT '',
                    owner_qn TEXT DEFAULT '',
                    extra_json TEXT DEFAULT '{{}}',
                    text_hash TEXT DEFAULT '',
                    search_text TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_vec_items_key ON vec_items(key);
                CREATE INDEX IF NOT EXISTS idx_vec_items_collection ON vec_items(collection);
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_index USING vec0(
                    embedding float[{self._dimension}]
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_fts USING fts5(
                    key UNINDEXED, collection UNINDEXED, search_text,
                    content='vec_items', content_rowid='rowid'
                );
            """)
            self._conn.commit()

    def add(self, key: str, collection: str, vector: list[float],
            metadata: dict, text_hash: str = "", search_text: str = ""):
        extra = json.dumps({k: v for k, v in metadata.items()
                            if k not in ("name", "category", "synonym", "owner_qn")},
                           ensure_ascii=False)
        name = metadata.get("name", "")
        category = metadata.get("category", "")
        synonym = metadata.get("synonym", "")
        owner_qn = metadata.get("owner_qn", "")

        with self._lock:
            row = self._conn.execute(
                "SELECT rowid FROM vec_items WHERE key = ?", (key,)
            ).fetchone()

            if row:
                rid = row[0]
                # Delete old FTS entry
                self._conn.execute(
                    "INSERT INTO vec_fts(vec_fts, rowid, key, collection, search_text) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rid, key, collection,
                     self._conn.execute("SELECT search_text FROM vec_items WHERE rowid=?",
                                        (rid,)).fetchone()[0])
                )
                self._conn.execute(
                    "UPDATE vec_items SET collection=?, name=?, category=?, "
                    "synonym=?, owner_qn=?, extra_json=?, text_hash=?, search_text=? "
                    "WHERE rowid=?",
                    (collection, name, category, synonym, owner_qn, extra, text_hash,
                     search_text, rid)
                )
                self._conn.execute(
                    "UPDATE vec_index SET embedding = ? WHERE rowid = ?",
                    (_serialize_f32(vector), rid)
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO vec_items (key, collection, name, category, synonym, "
                    "owner_qn, extra_json, text_hash, search_text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, collection, name, category, synonym, owner_qn, extra, text_hash,
                     search_text)
                )
                rid = cur.lastrowid
                self._conn.execute(
                    "INSERT INTO vec_index (rowid, embedding) VALUES (?, ?)",
                    (rid, _serialize_f32(vector))
                )

            # Insert FTS entry
            self._conn.execute(
                "INSERT INTO vec_fts(rowid, key, collection, search_text) VALUES(?, ?, ?, ?)",
                (rid, key, collection, search_text)
            )
            self._conn.commit()

    def add_many(self, collection: str,
                 items: list[tuple[str, dict, str, str]],
                 vectors: list[list[float]]):
        """Bulk insert/update — single transaction per chunk.

        items: list of (key, metadata, text_hash, search_text)
        vectors: aligned list of embedding vectors

        Designed for streaming pipelines where memory is bounded by chunk size.
        Avoids one-commit-per-row overhead of add() in tight loops.
        """
        if not items:
            return
        with self._lock:
            for (key, metadata, th, search_text), vec in zip(items, vectors):
                extra = json.dumps(
                    {k: v for k, v in metadata.items()
                     if k not in ("name", "category", "synonym", "owner_qn")},
                    ensure_ascii=False)
                name = metadata.get("name", "")
                category = metadata.get("category", "")
                synonym = metadata.get("synonym", "")
                owner_qn = metadata.get("owner_qn", "")

                row = self._conn.execute(
                    "SELECT rowid FROM vec_items WHERE key = ?", (key,)
                ).fetchone()

                if row:
                    rid = row[0]
                    old_text_row = self._conn.execute(
                        "SELECT search_text FROM vec_items WHERE rowid=?", (rid,)
                    ).fetchone()
                    if old_text_row:
                        self._conn.execute(
                            "INSERT INTO vec_fts(vec_fts, rowid, key, collection, search_text) "
                            "VALUES('delete', ?, ?, ?, ?)",
                            (rid, key, collection, old_text_row[0])
                        )
                    self._conn.execute(
                        "UPDATE vec_items SET collection=?, name=?, category=?, "
                        "synonym=?, owner_qn=?, extra_json=?, text_hash=?, search_text=? "
                        "WHERE rowid=?",
                        (collection, name, category, synonym, owner_qn, extra, th,
                         search_text, rid)
                    )
                    self._conn.execute(
                        "UPDATE vec_index SET embedding = ? WHERE rowid = ?",
                        (_serialize_f32(vec), rid)
                    )
                else:
                    cur = self._conn.execute(
                        "INSERT INTO vec_items (key, collection, name, category, synonym, "
                        "owner_qn, extra_json, text_hash, search_text) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, collection, name, category, synonym, owner_qn, extra, th,
                         search_text)
                    )
                    rid = cur.lastrowid
                    self._conn.execute(
                        "INSERT INTO vec_index (rowid, embedding) VALUES (?, ?)",
                        (rid, _serialize_f32(vec))
                    )

                self._conn.execute(
                    "INSERT INTO vec_fts(rowid, key, collection, search_text) VALUES(?, ?, ?, ?)",
                    (rid, key, collection, search_text)
                )
            self._conn.commit()

    def get_all_hashes(self, collection: str) -> dict[str, str]:
        """Return {key: text_hash} for an entire collection in one query.

        Used by streaming embedding pipelines to skip unchanged rows
        without paying for one SELECT per row.
        """
        rows = self._conn.execute(
            "SELECT key, text_hash FROM vec_items WHERE collection = ?",
            (collection,)
        ).fetchall()
        return {key: th for key, th in rows}

    def search(self, query_vector: list[float], collection: str,
               top_k: int = 15) -> list[dict]:
        query_bytes = _serialize_f32(query_vector)
        # vec0 KNN requires LIMIT as literal (not bind param) and no WHERE on joined tables
        # So: KNN subquery first, then filter by collection
        top_k = int(top_k)
        # Fetch more from KNN to account for collection filtering
        knn_limit = top_k * 5
        rows = self._conn.execute(f"""
            SELECT vi.key, vi.name, vi.category, vi.synonym, vi.owner_qn,
                   vi.extra_json, vx.distance
            FROM (
                SELECT rowid, distance
                FROM vec_index
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT {knn_limit}
            ) AS vx
            JOIN vec_items AS vi ON vi.rowid = vx.rowid
            WHERE vi.collection = ?
            ORDER BY vx.distance
            LIMIT {top_k}
        """, (query_bytes, collection)).fetchall()

        results = []
        for key, name, category, synonym, owner_qn, extra_json, distance in rows:
            score = round(1.0 - distance, 4)
            if score <= 0:
                continue  # filter out irrelevant results
            item = {
                "key": key, "name": name, "category": category,
                "synonym": synonym, "owner_qn": owner_qn,
                "score": score,
            }
            if extra_json and extra_json != "{}":
                item.update(json.loads(extra_json))
            results.append(item)
        return results

    # Common words that cause false positives in BM25
    _STOP_WORDS = frozenset({
        "для", "при", "без", "над", "под", "или", "как", "все", "это", "что",
        "его", "она", "они", "мой", "наш", "ваш", "тот", "где", "кто", "чем",
        "так", "уже", "еще", "ещё", "был", "быть", "будет", "есть", "нет",
        "работа", "работы", "работе", "данные", "данных", "значение", "значения",
        "объект", "объекта", "хранение", "хранения", "получение", "получения",
        "использование", "список", "форма", "формы", "модуль", "модуля",
        "the", "for", "and", "with", "from", "this", "that",
    })

    def search_hybrid(self, query_vector: list[float], query_text: str,
                       collection: str, top_k: int = 7,
                       embedding_weight: float = 0.8,
                       category_filter: str = "") -> list[dict]:
        """Hybrid search: combine embedding KNN with FTS5 BM25.

        If category_filter is set, only return results matching that category.
        """
        # Fetch more candidates when filtering by category
        fetch_mult = 5 if category_filter else 3

        # 1. Embedding search (primary)
        emb_results = self.search(query_vector, collection, top_k=top_k * fetch_mult)
        if category_filter:
            emb_results = [r for r in emb_results if r.get("category") == category_filter]
        emb_scores = {r["key"]: r["score"] for r in emb_results}
        emb_meta = {r["key"]: r for r in emb_results}

        # 2. FTS5 search — filter stop words, add Russian stem variants
        words = [w.strip() for w in query_text.split()
                 if len(w.strip()) >= 4 and w.strip().lower() not in self._STOP_WORDS]
        if words:
            # Add stem variants for Russian morphology (strip common suffixes)
            stems = set()
            for w in words:
                wl = w.lower()
                for suffix in ("ами", "ями", "ого", "его", "ому", "ему",
                               "ов", "ев", "ах", "ях", "ом", "ем", "ий",
                               "ей", "ам", "ям", "ой", "ый", "ая", "яя",
                               "ие", "ые", "ию", "ых", "их"):
                    if wl.endswith(suffix) and len(wl) - len(suffix) >= 4:
                        stem = wl[:-len(suffix)]
                        stems.add(stem)
                        break
            all_terms = list(set(words) | stems)
            fts_query = " OR ".join(all_terms)
            try:
                fts_rows = self._conn.execute(f"""
                    SELECT vi.key, rank
                    FROM vec_fts
                    JOIN vec_items AS vi ON vi.rowid = vec_fts.rowid
                    WHERE vec_fts MATCH ?
                      AND vi.collection = ?
                    ORDER BY rank
                    LIMIT {top_k * 5}
                """, (fts_query, collection)).fetchall()
            except Exception:
                fts_rows = []

            if category_filter:
                # Filter FTS results by category
                filtered_fts = []
                for key, rank in fts_rows:
                    cat_row = self._conn.execute(
                        "SELECT category FROM vec_items WHERE key = ?", (key,)
                    ).fetchone()
                    if cat_row and cat_row[0] == category_filter:
                        filtered_fts.append((key, rank))
                fts_rows = filtered_fts

            if fts_rows:
                # Normalize BM25 ranks to 0..1, penalize short names
                max_rank = max(abs(r[1]) for r in fts_rows) or 1
                fts_scores = {}
                for key, rank in fts_rows:
                    score = abs(rank) / max_rank
                    # Get name length for penalty
                    name_row = self._conn.execute(
                        "SELECT name FROM vec_items WHERE key = ?", (key,)
                    ).fetchone()
                    if name_row:
                        name_len = len(name_row[0])
                        if name_len < 10:
                            score *= 0.3  # heavy penalty for very short names
                        elif name_len < 15:
                            score *= min(1.0, name_len / 15)
                    fts_scores[key] = score
            else:
                fts_scores = {}
        else:
            fts_scores = {}

        # 2b. Direct name lookup — find objects whose name matches query words.
        # Catches cases where BM25 misses due to TF-IDF dilution on large corpora.
        # Only for "objects" collection (small, <10K). Skipped for routines (90K+).
        name_scores = {}
        q_words_lower = [w.strip().lower() for w in query_text.split()
                         if len(w.strip()) >= 4 and w.strip().lower() not in self._STOP_WORDS]
        if q_words_lower and collection == "objects":
            all_names = self._conn.execute(
                "SELECT key, name FROM vec_items WHERE collection = ?",
                (collection,)
            ).fetchall()
            for key, name in all_names:
                name_lower = name.lower()
                nm = sum(1 for w in q_words_lower if w[:5] in name_lower)
                if nm == 0:
                    continue
                name_word_count = max(1, sum(1 for c in name if c.isupper()))
                precision = nm / name_word_count
                if precision >= 0.5:
                    name_scores[key] = precision

        # 3. Combine scores with adaptive weights
        # If embedding found strong hits, trust it more; otherwise lean on BM25
        all_keys = set(emb_scores.keys()) | set(fts_scores.keys()) | set(name_scores.keys())
        max_emb = max(emb_scores.values()) if emb_scores else 0
        if max_emb > 0.2:
            emb_w, bm25_w = 0.85, 0.15  # strong embedding — BM25 as tiebreaker only
        else:
            emb_w, bm25_w = 0.5, 0.5    # weak embedding — BM25 helps
        combined = []
        for key in all_keys:
            e_score = emb_scores.get(key, 0)
            f_score = fts_scores.get(key, 0)
            n_score = name_scores.get(key, 0)
            if e_score > 0 and f_score > 0:
                final = e_score * emb_w + f_score * bm25_w
            elif f_score > 0:
                final = f_score
            else:
                final = e_score
            # Name match bonus: direct name precision adds to score
            if n_score > 0:
                final = max(final, n_score) + n_score * 0.5
            meta = emb_meta.get(key, {})
            if not meta and key in fts_scores:
                # FTS found it but embedding didn't — get metadata from DB
                row = self._conn.execute(
                    "SELECT key, name, category, synonym, owner_qn, extra_json "
                    "FROM vec_items WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    meta = {"key": row[0], "name": row[1], "category": row[2],
                            "synonym": row[3], "owner_qn": row[4]}
                    if row[5] and row[5] != "{}":
                        meta.update(json.loads(row[5]))
            item = {**meta, "score": round(final, 4)}
            combined.append(item)

        combined.sort(key=lambda x: x.get("score", 0), reverse=True)
        return combined[:top_k]

    def get_hash(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT text_hash FROM vec_items WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def get_all_keys(self, collection: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM vec_items WHERE collection = ?", (collection,)
        ).fetchall()
        return {r[0] for r in rows}

    def remove(self, key: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT rowid FROM vec_items WHERE key = ?", (key,)
            ).fetchone()
            if row:
                rid = row[0]
                self._conn.execute("DELETE FROM vec_items WHERE rowid = ?", (rid,))
                self._conn.execute("DELETE FROM vec_index WHERE rowid = ?", (rid,))
                self._conn.commit()

    def count(self, collection: Optional[str] = None) -> int:
        if collection:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM vec_items WHERE collection = ?", (collection,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()
        return row[0] if row else 0

    def clear(self, collection: Optional[str] = None):
        with self._lock:
            if collection:
                rows = self._conn.execute(
                    "SELECT rowid FROM vec_items WHERE collection = ?", (collection,)
                ).fetchall()
                rids = [r[0] for r in rows]
                if rids:
                    placeholders = ",".join("?" * len(rids))
                    self._conn.execute(
                        f"DELETE FROM vec_index WHERE rowid IN ({placeholders})", rids)
                    self._conn.execute(
                        f"DELETE FROM vec_items WHERE rowid IN ({placeholders})", rids)
            else:
                self._conn.execute("DELETE FROM vec_index")
                self._conn.execute("DELETE FROM vec_items")
            self._conn.commit()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Global singletons (initialized lazily from config)
# ---------------------------------------------------------------------------

_embedder: Optional[OnnxEmbedder] = None
_store: Optional[SqliteVecStore] = None
_reranker: Optional[CrossEncoderReranker] = None


def init(model_path: str, db_path: str, reranker_path: str = "",
         query_prefix: str = "", passage_prefix: str = ""):
    global _embedder, _store, _reranker
    try:
        _embedder = OnnxEmbedder(model_path, query_prefix=query_prefix,
                                  passage_prefix=passage_prefix)
        _store = SqliteVecStore(db_path, dimension=_embedder.dimension)
    except Exception:
        log.exception("Failed to initialize embedding service")
        _embedder = None
        _store = None

    if reranker_path and os.path.isdir(reranker_path):
        try:
            _reranker = CrossEncoderReranker(reranker_path)
        except Exception:
            log.exception("Failed to load cross-encoder reranker")
            _reranker = None


def get_embedder() -> Optional[OnnxEmbedder]:
    return _embedder


def get_store() -> Optional[SqliteVecStore]:
    return _store


def get_reranker() -> Optional[CrossEncoderReranker]:
    return _reranker


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
