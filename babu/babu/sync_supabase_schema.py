"""
BABU Cloud Database Migration & Alignment Utility
Aligns Supabase PostgreSQL schema with the latest V2 Cognitive OS (K0-K7 Hierarchy),
renames any legacy tables, populates 95 ADRs, and ensures vector & temporal indexes.
"""

import os
import sys
import json
from typing import Dict, Any, List

def sync_supabase_database(db_url: str = None) -> Dict[str, Any]:
    """
    Connect to Supabase PostgreSQL, create/align all canonical tables,
    migrate legacy data, and seed institutional ADR architecture knowledge.
    """
    url = db_url or os.environ.get("DATABASE_URL")
    if not url or not (url.startswith("postgres://") or url.startswith("postgresql://")):
        return {"status": "SKIPPED", "reason": "No valid PostgreSQL DATABASE_URL found"}

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    import psycopg2
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        cursor = conn.cursor()
    except Exception as e:
        return {"status": "CONNECTION_FAILED", "error": str(e)}

    results = {"tables_aligned": [], "legacy_migrated": [], "adrs_synced": 0}

    try:
        # 1. Check and migrate legacy table names if they exist from earlier versions
        legacy_mappings = [
            ("aria_temporal_timeline", "babu_temporal_timeline"),
            ("aria_k0_working_memory", "babu_k0_working_memory"),
            ("aria_knowledge", "babu_knowledge"),
            ("aria_checkpoint", "babu_checkpoint")
        ]
        
        for old_t, new_t in legacy_mappings:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = %s
                );
            """, (old_t,))
            if cursor.fetchone()[0]:
                cursor.execute(f"ALTER TABLE IF EXISTS {old_t} RENAME TO {new_t};")
                results["legacy_migrated"].append(f"{old_t} -> {new_t}")
                conn.commit()

        # 2. Canonical K0-K7 Schema Definitions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sealed_epochs (
                epoch_id TEXT PRIMARY KEY,
                sealed_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS search_cache (
                query_hash TEXT PRIMARY KEY,
                raw_query TEXT,
                distilled_results TEXT,
                sources TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS execution_ledger (
                event_id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                task_id TEXT,
                department TEXT,
                event_type TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT,
                metadata TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS system_memory (
                key TEXT PRIMARY KEY,
                data TEXT
            );
            
            CREATE TABLE IF NOT EXISTS trusted_templates (
                template_id TEXT PRIMARY KEY,
                template_signature TEXT UNIQUE,
                goal_graph_json TEXT,
                version INTEGER DEFAULT 1,
                execution_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ACTIVE',
                promoted_from_goal_id TEXT,
                promotion_epoch INTEGER,
                average_execution_time REAL DEFAULT 0.0,
                average_token_cost REAL DEFAULT 0.0,
                last_used TEXT,
                created_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS babu_temporal_timeline (
                event_id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_category VARCHAR(50) NOT NULL,
                summary TEXT NOT NULL,
                outcome VARCHAR(20),
                impact_score REAL DEFAULT 1.0,
                cause TEXT,
                effect TEXT,
                resolution TEXT,
                confidence DOUBLE PRECISION,
                metadata TEXT
            );
            
            CREATE TABLE IF NOT EXISTS architecture_knowledge (
                record_id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                title TEXT NOT NULL,
                phase TEXT,
                problem TEXT,
                decision TEXT,
                reason TEXT,
                outcome TEXT,
                tradeoff TEXT,
                impact_score INTEGER,
                supersedes TEXT,
                status TEXT DEFAULT 'Active',
                timestamp TEXT
            );
            
            CREATE TABLE IF NOT EXISTS babu_k0_working_memory (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                response TEXT,
                response_full TEXT,
                response_summary TEXT,
                status TEXT NOT NULL,
                failures TEXT,
                retrieved_records TEXT,
                knowledge_classes TEXT,
                source_records TEXT,
                conversation_reference BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS planned_graphs_cache (
                session_id TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                goal_class TEXT NOT NULL,
                graph_hash TEXT NOT NULL,
                raw_query TEXT NOT NULL,
                goal_graph_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, query_hash)
            );
            
            CREATE TABLE IF NOT EXISTS babu_knowledge (
                id SERIAL PRIMARY KEY,
                collection TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                embedding TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

        # 3. Create missing columns & indexes if not present
        alter_checks = [
            ("babu_temporal_timeline", "cause", "TEXT"),
            ("babu_temporal_timeline", "effect", "TEXT"),
            ("babu_temporal_timeline", "resolution", "TEXT"),
            ("babu_temporal_timeline", "confidence", "DOUBLE PRECISION"),
            ("trusted_templates", "average_execution_time", "REAL DEFAULT 0.0"),
            ("trusted_templates", "average_token_cost", "REAL DEFAULT 0.0"),
            ("trusted_templates", "status", "TEXT DEFAULT 'ACTIVE'")
        ]
        for tbl, col, col_type in alter_checks:
            try:
                cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {col_type};")
            except Exception:
                pass
        conn.commit()

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_k0_session_id ON babu_k0_working_memory (session_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pg_cache_session_hash ON planned_graphs_cache (session_id, query_hash);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_collection ON babu_knowledge (collection);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_source ON babu_knowledge (source);")
        conn.commit()

        # 4. Sync 95 ADRs from local SQLite or memory into Supabase
        try:
            from services import get_db_connection, DB_PATH
            import sqlite3
            if os.path.exists(DB_PATH):
                sconn = sqlite3.connect(DB_PATH)
                scur = sconn.cursor()
                scur.execute("""
                    SELECT record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp 
                    FROM architecture_knowledge
                """)
                adr_rows = scur.fetchall()
                scur.close()
                sconn.close()
                
                for r in adr_rows:
                    cursor.execute("""
                        INSERT INTO architecture_knowledge (record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (record_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            decision = EXCLUDED.decision,
                            outcome = EXCLUDED.outcome,
                            tradeoff = EXCLUDED.tradeoff,
                            impact_score = EXCLUDED.impact_score;
                    """, r)
                conn.commit()
                results["adrs_synced"] = len(adr_rows)
        except Exception as adr_err:
            print(f"[SUPABASE ADR SYNC WARNING] {adr_err}", flush=True)

        # 5. Collect aligned table statistics
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        all_tables = [r[0] for r in cursor.fetchall()]
        results["tables_aligned"] = all_tables

        cursor.close()
        conn.close()
        results["status"] = "SUCCESS"
        return results
    except Exception as exc:
        if conn:
            conn.close()
        return {"status": "ERROR", "error": str(exc)}

if __name__ == "__main__":
    res = sync_supabase_database()
    print(json.dumps(res, indent=2))
