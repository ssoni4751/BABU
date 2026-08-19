"""
rag_storage.py — BABU Self-Awareness Knowledge Layer Vector Database Storage Engine

This module implements:
1. Resilient embedding generation factory (Gemini -> Mock/Fake fallback).
2. Multi-backend schema setup (Supabase pgvector -> Local SQLite).
3. Hybrid semantic search (SQL vector matching on Postgres, Python Cosine Similarity on SQLite).
4. Strict retrieval limits (MAX_RESULTS = 3, MAX_RETRIEVED_TOKENS = 1200).
"""

import os
import json
import re
import math
import hashlib
import random
from typing import Any, Optional, Union

try:
    from .services import get_db_connection
except ImportError:
    from services import get_db_connection


def _get_db_connection():
    try:
        from . import bot as bot_module
        return bot_module.get_db_connection()
    except Exception:
        return get_db_connection()

# Define constraints
MAX_RESULTS = 3
MAX_RETRIEVED_TOKENS = 1200

class MockEmbeddings:
    """Deterministic, keyless pseudo-random embedding generator for offline testing."""
    
    def embed_query(self, text: str) -> list[float]:
        if not text:
            return [0.0] * 768
        # Extract keywords to add semantic signals to the mock embedding
        words = re.findall(r'\w+', text.lower())
        h = hashlib.sha256(text.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(h, "big"))
        base_vector = [rng.uniform(-0.5, 0.5) for _ in range(768)]
        
        # Adjust embedding based on keyword hashes to simulate semantic clustering
        for word in words:
            word_hash = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            # Seed word rng
            word_rng = random.Random(word_hash)
            for _ in range(20):
                idx = word_rng.randint(0, 767)
                base_vector[idx] += word_rng.uniform(-0.1, 0.1)
                
        # Normalize the vector to unit length
        norm = sum(x * x for x in base_vector) ** 0.5
        if norm > 0:
            base_vector = [x / norm for x in base_vector]
        return base_vector

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        return [self.embed_query(doc) for doc in documents]


_GEMINI_FAILED = False


class ResilientEmbeddings:
    """A wrapper embedding model that dynamically falls back across models and defaults to Mock."""
    def embed_query(self, text: str) -> list[float]:
        global _GEMINI_FAILED
        # 1. Try Gemini if it has not failed in this process run
        if os.environ.get("GEMINI_API_KEY") and not _GEMINI_FAILED:
            try:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings
                # Try primary model
                try:
                    model = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001", output_dimensionality=768)
                    return model.embed_query(text)
                except Exception as gemini_err1:
                    print(f"[RAG] Gemini model gemini-embedding-001 failed: {gemini_err1}. Trying models/text-embedding-004...", flush=True)
                    model = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004", output_dimensionality=768)
                    return model.embed_query(text)
            except Exception as e:
                print(f"[RAG ERROR] Gemini embedding initialization or generation failed: {e}. Falling back to MockEmbeddings for the rest of this process run.", flush=True)
                _GEMINI_FAILED = True

        # 2. Fallback to Mock
        return MockEmbeddings().embed_query(text)

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        return [self.embed_query(doc) for doc in documents]


def get_embeddings_model() -> Any:
    """Resilient factory returning an embedding model."""
    return ResilientEmbeddings()


def get_embedding(text: str) -> list[float]:
    """Generate embedding vector for *text*."""
    model = get_embeddings_model()
    try:
        return model.embed_query(text)
    except Exception as e:
        print(f"[RAG ERROR] Embedding generation failed: {e}. Falling back to MockEmbeddings.", flush=True)
        return MockEmbeddings().embed_query(text)


_RAG_DB_INITIALIZED = False


def init_rag_db(force: bool = False):
    """Register pgvector or SQLite schemas based on DATABASE_URL availability."""
    global _RAG_DB_INITIALIZED
    if _RAG_DB_INITIALIZED and not force:
        return
        
    conn, is_pg = _get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres / Supabase
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Fetch default vector dimension from model (text-embedding-004 yields 768)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS babu_knowledge (
                    id SERIAL PRIMARY KEY,
                    collection VARCHAR(50) NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    embedding vector(768),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_collection ON babu_knowledge (collection);")
        else:
            # SQLite fallback
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS babu_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    embedding TEXT NOT NULL, -- JSON string serialized vector
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_collection ON babu_knowledge (collection);")
        conn.commit()
        cursor.close()
        conn.close()
        print("[RAG] Knowledge base schema initialized successfully.", flush=True)
        _RAG_DB_INITIALIZED = True
    except Exception as e:
        print(f"[RAG ERROR] init_rag_db failed: {e}", flush=True)


def store_knowledge_chunk(collection: str, source: str, title: str, chunk_text: str, metadata: Optional[dict] = None):
    """Embed and store a single knowledge chunk into database."""
    if not chunk_text or not chunk_text.strip():
        return
        
    init_rag_db()
    vector = get_embedding(chunk_text)
    meta_dict = metadata or {}



    conn, is_pg = _get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres pgvector insert
            cursor.execute("""
                INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (collection, source, title, chunk_text, vector, json.dumps(meta_dict)))
        else:
            # SQLite insert
            cursor.execute("""
                INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (collection, source, title, chunk_text, json.dumps(vector), json.dumps(meta_dict)))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[RAG ERROR] store_knowledge_chunk failed: {e}", flush=True)


def compute_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Pure-Python cosine similarity computation."""
    if len(v1) != len(v2):
        # Truncate or pad to match dimensions (defensive check)
        min_len = min(len(v1), len(v2))
        v1 = v1[:min_len]
        v2 = v2[:min_len]
        
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def retrieve_knowledge(query: str, collections: Optional[list[str]] = None, top_k: int = 3, similarity_threshold: float = 0.35, sources: Optional[list[str]] = None, query_vector: Optional[list[float]] = None) -> list[dict]:
    """Retrieve top matched knowledge chunks, strictly enforcing token budget constraints."""
    init_rag_db()
    
    if query_vector is None:
        query_vector = get_embedding(query)
    results = []



    conn, is_pg = _get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres pgvector search
            # Cosine distance: <=>
            # Cosine similarity = 1 - Cosine distance
            if collections or sources:
                clauses = []
                params = [query_vector]
                if collections:
                    clauses.append("collection = ANY(%s)")
                    params.append(collections)
                if sources:
                    clauses.append("source = ANY(%s)")
                    params.append(sources)
                
                where_clause = " AND ".join(clauses)
                params.extend([query_vector, similarity_threshold, top_k * 2])
                cursor.execute(f"""
                    SELECT id, collection, source, title, chunk_text, metadata, (1 - (embedding <=> %s::vector)) AS similarity
                    FROM babu_knowledge
                    WHERE {where_clause} AND (1 - (embedding <=> %s::vector)) >= %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """, params)
            else:
                cursor.execute("""
                    SELECT id, collection, source, title, chunk_text, metadata, (1 - (embedding <=> %s::vector)) AS similarity
                    FROM babu_knowledge
                    WHERE (1 - (embedding <=> %s::vector)) >= %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """, (query_vector, query_vector, similarity_threshold, top_k * 2))
            
            rows = cursor.fetchall()
            for r in rows:
                results.append({
                    "id": r[0],
                    "collection": r[1],
                    "source": r[2],
                    "title": r[3],
                    "chunk_text": r[4],
                    "metadata": r[5] if isinstance(r[5], dict) else json.loads(r[5] or '{}'),
                    "similarity": float(r[6])
                })
        else:
            # SQLite programmatic similarity fallback
            if collections or sources:
                clauses = []
                params = []
                if collections:
                    placeholders = ",".join("?" for _ in collections)
                    clauses.append(f"collection IN ({placeholders})")
                    params.extend(collections)
                if sources:
                    placeholders_src = ",".join("?" for _ in sources)
                    clauses.append(f"source IN ({placeholders_src})")
                    params.extend(sources)
                
                where_clause = " AND ".join(clauses)
                cursor.execute(f"""
                    SELECT id, collection, source, title, chunk_text, embedding, metadata
                    FROM babu_knowledge
                    WHERE {where_clause}
                """, params)
            else:
                cursor.execute("""
                    SELECT id, collection, source, title, chunk_text, embedding, metadata
                    FROM babu_knowledge
                """)
            
            rows = cursor.fetchall()
            candidates = []
            for r in rows:
                try:
                    db_vector = json.loads(r[5])
                    similarity = compute_cosine_similarity(query_vector, db_vector)
                    if similarity >= similarity_threshold:
                        candidates.append({
                            "id": r[0],
                            "collection": r[1],
                            "source": r[2],
                            "title": r[3],
                            "chunk_text": r[4],
                            "metadata": json.loads(r[6] or '{}'),
                            "similarity": similarity
                        })
                except Exception as parse_err:
                    continue
            
            # Sort by similarity descending
            candidates.sort(key=lambda x: x["similarity"], reverse=True)
            results = candidates[:top_k * 2]
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[RAG ERROR] retrieve_knowledge failed: {e}", flush=True)
        
    # 3. Fallback to simple keyword/substring search if vector similarity yields no matches or low match score.
    # This acts as a bulletproof fallback when embedding API rate limits or quota errors occur.
    import re
    if not results or max(x["similarity"] for x in results) < 0.25:
        print("[RAG] Vector search produced low similarity or no matches. Running text search fallback.", flush=True)
        # Tokenize query into alphanumeric keywords of length > 3
        keywords = [w.strip() for w in re.split(r'\W+', query) if len(w.strip()) > 3]
        if keywords:
            try:
                conn, is_pg = _get_db_connection()
                cursor = conn.cursor()
                if is_pg:
                    like_clauses = " OR ".join(["chunk_text ILIKE %s" for _ in keywords])
                    if collections or sources:
                        clauses = []
                        params = []
                        if collections:
                            clauses.append("collection = ANY(%s)")
                            params.append(collections)
                        if sources:
                            clauses.append("source = ANY(%s)")
                            params.append(sources)
                        
                        where_clause = " AND ".join(clauses)
                        cursor.execute(f"""
                            SELECT id, collection, source, title, chunk_text, metadata, 0.49 AS similarity
                            FROM babu_knowledge
                            WHERE {where_clause} AND ({like_clauses})
                            LIMIT %s
                        """, (*params, *[f"%{kw}%" for kw in keywords], top_k * 2))
                    else:
                        cursor.execute(f"""
                            SELECT id, collection, source, title, chunk_text, metadata, 0.49 AS similarity
                            FROM babu_knowledge
                            WHERE {like_clauses}
                            LIMIT %s
                        """, (*[f"%{kw}%" for kw in keywords], top_k * 2))
                    rows = cursor.fetchall()
                    for r in rows:
                        results.append({
                            "id": r[0],
                            "collection": r[1],
                            "source": r[2],
                            "title": r[3],
                            "chunk_text": r[4],
                            "metadata": r[5] if isinstance(r[5], dict) else json.loads(r[5] or '{}'),
                            "similarity": 0.49
                        })
                else:
                    like_clauses = " OR ".join(["chunk_text LIKE ?" for _ in keywords])
                    if collections or sources:
                        clauses = []
                        params = []
                        if collections:
                            placeholders = ",".join("?" for _ in collections)
                            clauses.append(f"collection IN ({placeholders})")
                            params.extend(collections)
                        if sources:
                            placeholders_src = ",".join("?" for _ in sources)
                            clauses.append(f"source IN ({placeholders_src})")
                            params.extend(sources)
                        
                        where_clause = " AND ".join(clauses)
                        cursor.execute(f"""
                            SELECT id, collection, source, title, chunk_text, metadata, 0.49 AS similarity
                            FROM babu_knowledge
                            WHERE {where_clause} AND ({like_clauses})
                            LIMIT ?
                        """, (*params, *[f"%{kw}%" for kw in keywords], top_k * 2))
                    else:
                        cursor.execute(f"""
                            SELECT id, collection, source, title, chunk_text, metadata, 0.49 AS similarity
                            FROM babu_knowledge
                            WHERE {like_clauses}
                            LIMIT ?
                        """, (*[f"%{kw}%" for kw in keywords], top_k * 2))
                    rows = cursor.fetchall()
                    for r in rows:
                        results.append({
                            "id": r[0],
                            "collection": r[1],
                            "source": r[2],
                            "title": r[3],
                            "chunk_text": r[4],
                            "metadata": json.loads(r[5] or '{}'),
                            "similarity": 0.49
                        })
                cursor.close()
                conn.close()
            except Exception as fe:
                print(f"[RAG WARNING] Text search fallback failed: {fe}", flush=True)

    # 4. Enforce strict budget: MAX_RESULTS = 3, MAX_RETRIEVED_TOKENS = 1200
    final_results = []
    total_tokens = 0
    
    # Sort results to get the highest similarity first
    results.sort(key=lambda x: x["similarity"], reverse=True)

    for item in results:
        if len(final_results) >= MAX_RESULTS:
            break
            
        text = item["chunk_text"]
        # Estimate tokens (1 token ≈ 4 characters)
        estimated_tokens = len(text) // 4
        
        if total_tokens + estimated_tokens > MAX_RETRIEVED_TOKENS:
            # If the first result itself is too large, truncate it to fit the budget.
            # Otherwise, skip this block.
            remaining_tokens = MAX_RETRIEVED_TOKENS - total_tokens
            if remaining_tokens > 100:  # Only chunk if we can fit a substantial portion
                chunk_limit_char = remaining_tokens * 4
                item["chunk_text"] = text[:chunk_limit_char] + "\n... (truncated due to token budget limits)"
                final_results.append(item)
                total_tokens = MAX_RETRIEVED_TOKENS
            break
        else:
            final_results.append(item)
            total_tokens += estimated_tokens

    return final_results


def retrieve_system_knowledge_hierarchical(
    query: str,
    matched_books: Optional[list[str]] = None,
    top_k: int = 3,
    similarity_threshold: float = 0.35
) -> list[dict]:
    """
    Tiered retrieval for system queries:
    1. See index first (System_Information_Index.md / system_index collection).
    2. Then books (matched_books / adr_books collection).
    3. Then the rest of RAG (general collections like babu_docs, engineering_history, immune_lessons, telemetry_knowledge, governance).
    Enforces the overall MAX_RESULTS and MAX_RETRIEVED_TOKENS limits.
    """
    query_vector = get_embedding(query)
    
    # 1. Tier 1: Index
    index_results = retrieve_knowledge(
        query=query,
        collections=["system_index"],
        sources=["System_Information_Index.md"],
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        query_vector=query_vector
    )
    
    # 2. Tier 2: Books
    book_results = []
    if matched_books:
        book_results = retrieve_knowledge(
            query=query,
            collections=["adr_books", "babu_docs"],
            sources=matched_books,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            query_vector=query_vector
        )
    else:
        book_results = retrieve_knowledge(
            query=query,
            collections=["adr_books"],
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            query_vector=query_vector
        )
        
    # 3. Tier 3: General RAG
    rag_results = retrieve_knowledge(
        query=query,
        collections=["babu_docs", "engineering_history", "immune_lessons", "telemetry_knowledge", "governance"],
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        query_vector=query_vector
    )
    
    # Combine results in priority order
    combined_candidates = []
    seen_ids = set()
    seen_chunks = set()
    
    def add_candidates(items):
        for item in items:
            item_id = item.get("id")
            chunk_hash = hashlib.sha256(item["chunk_text"].strip().encode("utf-8")).hexdigest()
            if item_id in seen_ids or chunk_hash in seen_chunks:
                continue
            seen_ids.add(item_id)
            seen_chunks.add(chunk_hash)
            combined_candidates.append(item)

    add_candidates(index_results)
    add_candidates(book_results)
    add_candidates(rag_results)
    
    # Enforce strict budget: MAX_RESULTS = 3, MAX_RETRIEVED_TOKENS = 1200
    final_results = []
    total_tokens = 0
    
    for item in combined_candidates:
        if len(final_results) >= MAX_RESULTS:
            break
            
        text = item["chunk_text"]
        estimated_tokens = len(text) // 4
        
        if total_tokens + estimated_tokens > MAX_RETRIEVED_TOKENS:
            remaining_tokens = MAX_RETRIEVED_TOKENS - total_tokens
            if remaining_tokens > 100:
                chunk_limit_char = remaining_tokens * 4
                item["chunk_text"] = text[:chunk_limit_char] + "\n... (truncated due to token budget limits)"
                final_results.append(item)
                total_tokens = MAX_RETRIEVED_TOKENS
            break
        else:
            final_results.append(item)
            total_tokens += estimated_tokens
            
    return final_results
