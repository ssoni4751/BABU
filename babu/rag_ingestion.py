"""
rag_ingestion.py — Ingestion Pipeline for BABU Self-Awareness Knowledge Layer

This script:
1. Locates markdown artifacts, codebase scripts, E0 config JSONs, and active database templates.
2. Chunks files using domain-specific section/rule splitting.
3. Computes embeddings and stores knowledge chunks inside the babu_knowledge table.
"""

import os
import json
import glob
from typing import Optional

try:
    from .rag_storage import store_knowledge_chunk, init_rag_db
    from .services import get_db_connection
except ImportError:
    from rag_storage import store_knowledge_chunk, init_rag_db
    from services import get_db_connection

def chunk_markdown_by_headings(content: str) -> list[dict]:
    """Split markdown documents by section headings (# or ## or ###)."""
    chunks = []
    current_title = "Introduction"
    current_text = []
    
    lines = content.split("\n")
    for line in lines:
        if line.startswith("#"):
            if current_text:
                chunks.append({
                    "title": current_title,
                    "text": "\n".join(current_text).strip()
                })
            # Remove symbols
            current_title = line.lstrip("#").strip()
            current_text = [line]
        else:
            current_text.append(line)
            
    if current_text:
        chunks.append({
            "title": current_title,
            "text": "\n".join(current_text).strip()
        })
    return chunks


def ingest_markdown_file(filepath: str, collection: str):
    """Chunk and ingest a single markdown document."""
    if not os.path.exists(filepath):
        print(f"[INGEST] File not found: {filepath}. Skipping.", flush=True)
        return
        
    print(f"[INGEST] Reading markdown file: {filepath}...", flush=True)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    chunks = chunk_markdown_by_headings(content)
    filename = os.path.basename(filepath)
    
    for chunk in chunks:
        title = chunk["title"]
        text = chunk["text"]
        if len(text.strip()) < 40:
            continue  # Skip header-only or blank chunks
            
        store_knowledge_chunk(
            collection=collection,
            source=filename,
            title=title,
            chunk_text=text,
            metadata={"source_file": filename, "section": title}
        )
    print(f"[INGEST] Successfully ingested {len(chunks)} chunks from {filename} into '{collection}'.", flush=True)


def ingest_e0_configs(config_dir: str):
    """Ingest constitution and policy parameters (one chunk per policy rule)."""
    # 1. Constitution
    const_path = os.path.join(config_dir, "constitution.json")
    if os.path.exists(const_path):
        print(f"[INGEST] Reading constitution config: {const_path}...", flush=True)
        with open(const_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Ingest safety/truthfulness guidelines
        for key in ["truthfulness", "audit_requirements", "micro_auditor_boundary"]:
            if key in data:
                store_knowledge_chunk(
                    collection="governance",
                    source="constitution.json",
                    title=f"Constitution Policy: {key}",
                    chunk_text=f"Constitution {key.replace('_', ' ').capitalize()}: {data[key]}",
                    metadata={"config": "constitution", "rule": key}
                )
            
        # Ingest execution boundaries
        boundaries = data.get("execution_boundaries", [])
        if isinstance(boundaries, list):
            for action in boundaries:
                store_knowledge_chunk(
                    collection="governance",
                    source="constitution.json",
                    title=f"Execution Boundary: {action}",
                    chunk_text=f"Constitution Execution Boundary allowed action: {action}",
                    metadata={"config": "constitution", "rule": action}
                )
        elif isinstance(boundaries, dict):
            for name, detail in boundaries.items():
                store_knowledge_chunk(
                    collection="governance",
                    source="constitution.json",
                    title=f"Execution Boundary: {name}",
                    chunk_text=f"Constitution Execution Boundary [{name}]: {detail}",
                    metadata={"config": "constitution", "rule": name}
                )
            
    # 2. Policies
    policies_path = os.path.join(config_dir, "policies.json")
    if os.path.exists(policies_path):
        print(f"[INGEST] Reading policies config: {policies_path}...", flush=True)
        with open(policies_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        for name, value in data.items():
            store_knowledge_chunk(
                collection="governance",
                source="policies.json",
                title=f"E[Temp] Policy: {name}",
                chunk_text=f"Compiled Cognition E0 Policy Parameter [{name}] threshold set to: {value}",
                metadata={"config": "policies", "rule": name}
            )


def ingest_immune_lessons_db():
    """Extract and ingest immune anti-pattern rules from the system memory database table."""
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT key, data FROM system_memory WHERE key LIKE 'anti_pattern_%' OR key = 'immune_rules'")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for r in rows:
            key = r[0]
            val = r[1]
            store_knowledge_chunk(
                collection="immune_lessons",
                source="system_memory",
                title=f"Immune Pattern: {key}",
                chunk_text=f"Immune System Diagnostic / Historical Anti-pattern [{key}]: {val}",
                metadata={"key": key}
            )
        print(f"[INGEST] Ingested {len(rows)} immune anti-pattern rules from system_memory.", flush=True)
    except Exception as e:
        print(f"[INGEST WARNING] Failed to ingest immune memory rules: {e}", flush=True)


def ingest_etemp_templates():
    """Extract and ingest promoted trusted templates from the templates registry database table."""
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT template_id, template_signature, version, execution_count, success_count, status FROM trusted_templates")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for r in rows:
            tid = r[0]
            sig = r[1]
            ver = r[2]
            exec_cnt = r[3]
            succ_cnt = r[4]
            status = r[5]
            
            chunk_content = (
                f"Trusted E[Temp] Template '{tid}' with signature '{sig}'.\n"
                f"Status: {status}\n"
                f"Version: {ver}\n"
                f"Performance Metrics: Executed {exec_cnt} times, Succeeded {succ_cnt} times."
            )
            store_knowledge_chunk(
                collection="etemp",
                source="trusted_templates",
                title=f"Template: {tid}",
                chunk_text=chunk_content,
                metadata={"template_id": tid, "signature": sig}
            )
        print(f"[INGEST] Ingested {len(rows)} promoted execution templates.", flush=True)
    except Exception as e:
        print(f"[INGEST WARNING] Failed to ingest database templates: {e}", flush=True)


def run_full_ingestion(artifact_dir: str, project_dir: str):
    """Run the complete chunking and vector loading pipeline."""
    init_rag_db()
    
    # 1. Ingest Documentation & Engineering History (Markdown Artifacts)
    markdown_files = [
        ("babu_cognitive_os_architectural_blueprint.md", "babu_docs"),
        ("retrieval_and_auditability_assessment.md", "babu_docs"),
        ("orchestration_architecture_report.md", "babu_docs"),
        ("model_architecture_report.md", "babu_docs"),
        ("governance_separation_plan.md", "babu_docs"),
        ("babu_execution_flow.md", "babu_docs"),
        ("babu_immune_rules_report.md", "immune_lessons"),
        ("babu_latency_analysis_report.md", "telemetry_knowledge"),
        ("walkthrough.md", "engineering_history"),
        ("implementation_plan.md", "engineering_history"),
        ("System_Information_Index.md", "system_index"),
        ("BABU_ADR_Book_v1.md", "adr_books"),
        ("BABU_ADR_Book_v2.md", "adr_books"),
        ("BABU_ADR_Book_v3.md", "adr_books"),
        ("BABU_ADR_Book_v4.md", "adr_books")
    ]
    
    for filename, collection in markdown_files:
        filepath = os.path.join(artifact_dir, filename)
        if os.path.exists(filepath):
            ingest_markdown_file(filepath, collection)
        else:
            # Fall back to checking project folder
            proj_filepath = os.path.join(project_dir, filename)
            ingest_markdown_file(proj_filepath, collection)

    # 2. Ingest E0 Config JSONs
    e0_dir = os.path.join(project_dir, "babu", "e0")
    if os.path.exists(e0_dir):
        ingest_e0_configs(e0_dir)
    else:
        # Check standard path
        ingest_e0_configs(os.path.join(project_dir, "e0"))

    # 3. Ingest Database Records
    ingest_immune_lessons_db()
    ingest_etemp_templates()
    
    print("[INGESTION PIPELINE] Complete.", flush=True)

if __name__ == "__main__":
    # Script entry point
    # Dynamically resolve project directory as parent of 'babu' folder
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    project_path = os.path.dirname(current_file_dir)
    # Use workspace brain or fallback
    artifact_path = os.path.join(os.path.dirname(project_path), "brain")
    if not os.path.exists(artifact_path):
        artifact_path = project_path
        
    print(f"[INGESTION] Running with project_path={project_path}, artifact_path={artifact_path}", flush=True)
    run_full_ingestion(artifact_path, project_path)
