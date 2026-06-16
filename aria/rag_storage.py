"""
rag_storage.py — ARIA Self-Awareness Knowledge Layer Vector Database Storage Engine

This module implements:
1. Resilient embedding generation factory (Gemini -> OpenAI -> Mock/Fake fallback).
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


def get_embeddings_model() -> Any:
    """Resilient factory returning an embedding model."""
    # 1. Check Gemini
    if os.environ.get("GEMINI_API_KEY"):
        try:
            from langchain_google_genai import GoogleGenAIEmbeddings
            return GoogleGenAIEmbeddings(model="models/text-embedding-004")
        except Exception as e:
            print(f"[RAG] Failed to load GoogleGenAIEmbeddings: {e}. Trying OpenAI fallback.", flush=True)

    # 2. Check OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from langchain_openai import OpenAIEmbeddings
            return OpenAIEmbeddings(model="text-embedding-3-small")
        except Exception as e:
            print(f"[RAG] Failed to load OpenAIEmbeddings: {e}. Falling back to Mock.", flush=True)

    # 3. Fallback to Mock
    return MockEmbeddings()


def get_embedding(text: str) -> list[float]:
    """Generate embedding vector for *text*."""
    model = get_embeddings_model()
    try:
        return model.embed_query(text)
    except Exception as e:
        print(f"[RAG ERROR] Embedding generation failed: {e}. Falling back to MockEmbeddings.", flush=True)
        return MockEmbeddings().embed_query(text)


def init_rag_db():
    """Register pgvector or SQLite schemas based on DATABASE_URL availability."""
    try:
        from .bot import get_db_connection
    except ImportError:
        from bot import get_db_connection

    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres / Supabase
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Fetch default vector dimension from model (text-embedding-004 yields 768)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aria_knowledge (
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_aria_knowledge_collection ON aria_knowledge (collection);")
        else:
            # SQLite fallback
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aria_knowledge (
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_aria_knowledge_collection ON aria_knowledge (collection);")
        conn.commit()
        cursor.close()
        conn.close()
        print("[RAG] Knowledge base schema initialized successfully.", flush=True)
    except Exception as e:
        print(f"[RAG ERROR] init_rag_db failed: {e}", flush=True)


def store_knowledge_chunk(collection: str, source: str, title: str, chunk_text: str, metadata: Optional[dict] = None):
    """Embed and store a single knowledge chunk into database."""
    if not chunk_text or not chunk_text.strip():
        return
        
    init_rag_db()
    vector = get_embedding(chunk_text)
    meta_dict = metadata or {}

    try:
        from .bot import get_db_connection
    except ImportError:
        from bot import get_db_connection

    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres pgvector insert
            cursor.execute("""
                INSERT INTO aria_knowledge (collection, source, title, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (collection, source, title, chunk_text, vector, json.dumps(meta_dict)))
        else:
            # SQLite insert
            cursor.execute("""
                INSERT INTO aria_knowledge (collection, source, title, chunk_text, embedding, metadata)
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


def retrieve_knowledge(query: str, collections: Optional[list[str]] = None, top_k: int = 3, similarity_threshold: float = 0.35) -> list[dict]:
    """Retrieve top matched knowledge chunks, strictly enforcing token budget constraints."""
    init_rag_db()
    
    query_vector = get_embedding(query)
    results = []

    try:
        from .bot import get_db_connection
    except ImportError:
        from bot import get_db_connection

    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            # Postgres pgvector search
            # Cosine distance: <=>
            # Cosine similarity = 1 - Cosine distance
            if collections:
                cursor.execute("""
                    SELECT id, collection, source, title, chunk_text, metadata, (1 - (embedding <=> %s::vector)) AS similarity
                    FROM aria_knowledge
                    WHERE collection = ANY(%s) AND (1 - (embedding <=> %s::vector)) >= %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """, (query_vector, collections, query_vector, similarity_threshold, top_k * 2))
            else:
                cursor.execute("""
                    SELECT id, collection, source, title, chunk_text, metadata, (1 - (embedding <=> %s::vector)) AS similarity
                    FROM aria_knowledge
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
            if collections:
                placeholders = ",".join("?" for _ in collections)
                cursor.execute(f"""
                    SELECT id, collection, source, title, chunk_text, embedding, metadata
                    FROM aria_knowledge
                    WHERE collection IN ({placeholders})
                """, collections)
            else:
                cursor.execute("""
                    SELECT id, collection, source, title, chunk_text, embedding, metadata
                    FROM aria_knowledge
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
