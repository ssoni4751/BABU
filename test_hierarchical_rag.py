import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

# Ensure babu is in python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
babu_dir = os.path.join(CURRENT_DIR, "babu")
if babu_dir not in sys.path:
    sys.path.append(babu_dir)

from babu.rag_storage import init_rag_db, retrieve_system_knowledge_hierarchical, MockEmbeddings
from babu.services import get_db_connection
from babu.planner import classify_intent

def setup_module(module):
    # Ensure SQLite db is used for test
    os.environ["DATABASE_URL"] = "sqlite:///test_hierarchical.db"
    init_rag_db()
    
    # Clear and seed test database
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM babu_knowledge;")
        
        embed = MockEmbeddings()
        # Seed Tier 1: Index
        vec_index = embed.embed_query("system architecture index details")
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("system_index", "System_Information_Index.md", "SII Section", "This is the System Information Index overview chunk details on system architecture.", json.dumps(vec_index), "{}")
        )
        
        # Seed Tier 2: Books
        vec_book = embed.embed_query("system architecture books and adr details")
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("adr_books", "BABU_ADR_Book_v1.md", "ADR Book V1 Section", "This is the ADR book volume 1 containing constitution details and system architecture.", json.dumps(vec_book), "{}")
        )
        
        # Seed Tier 3: General RAG
        vec_rag = embed.embed_query("system architecture general blueprint details")
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("babu_docs", "babu_cognitive_os_architectural_blueprint.md", "Blueprint doc", "This is the cognitive OS architectural blueprint document chunk with system architecture details.", json.dumps(vec_rag), "{}")
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def teardown_module(module):
    if os.path.exists("test_hierarchical.db"):
        try:
            os.remove("test_hierarchical.db")
        except Exception:
            pass

def test_hierarchical_retrieval_order():
    """Verify that retrieve_system_knowledge_hierarchical prioritizes Index -> Books -> RAG."""
    # We query for general system architecture details which is semantic to all three seeded chunks
    results = retrieve_system_knowledge_hierarchical(
        query="system architecture details",
        matched_books=["BABU_ADR_Book_v1.md"],
        top_k=3,
        similarity_threshold=0.1 # low threshold to ensure all are fetched
    )
    
    assert len(results) == 3
    # First must be the index section (Tier 1)
    assert results[0]["collection"] == "system_index"
    assert results[0]["source"] == "System_Information_Index.md"
    
    # Second must be the book (Tier 2)
    assert results[1]["collection"] == "adr_books"
    assert results[1]["source"] == "BABU_ADR_Book_v1.md"
    
    # Third must be the general RAG (Tier 3)
    assert results[2]["collection"] == "babu_docs"
    assert results[2]["source"] == "babu_cognitive_os_architectural_blueprint.md"

def test_intent_classification_system_query_with_execution():
    """Verify that a system-aware query asking for Google Drive uploads preserves execution and sets APPROVAL_REQUIRED."""
    query = "analyse your architecture and save report to google drive"
    packet = classify_intent(query)
    
    # Should be classified as system query
    assert packet.system_query is True
    assert packet.query_category == "SYSTEM_INFORMATION"
    
    # Should allow execution department and upload_to_drive action
    assert "execution" in packet.allowed_departments
    assert "upload_to_drive" in packet.allowed_actions
    
    # Must enforce APPROVAL_REQUIRED for safety
    assert packet.execution_mode == "APPROVAL_REQUIRED"

def test_intent_classification_system_query_without_execution():
    """Verify that a system-aware query without explicit execution requests stays READ_ONLY and strips execution."""
    query = "analyse your architecture and explain how the router works"
    packet = classify_intent(query)
    
    assert packet.system_query is True
    assert packet.execution_mode == "READ_ONLY"
    assert "execution" not in packet.allowed_departments
    assert len(packet.allowed_actions) == 0
