import os
import sys
import pytest
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 encoding for Windows standard streams
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.bot import is_private_data_query, web_search, REFUSAL_PRIVATE_DATA
from babu.planner import classify_intent, IntentPacket
from babu.task_engine import TaskDTO
from babu.workers import run_worker
from babu.auditor import PostExecutionValidator

# Use model name from bot config
try:
    from babu.bot import CURRENT_DEPT_MODEL, llm_dept
except ImportError:
    from bot import CURRENT_DEPT_MODEL, llm_dept

def test_is_private_data_query():
    """Verify that private data queries are correctly identified."""
    # Private queries (possessives, specific topics, categories)
    assert is_private_data_query("mere business me kitne clients hain?") is True
    assert is_private_data_query("my clients and their names") is True
    assert is_private_data_query("PF claim list for client Shubham") is True
    assert is_private_data_query("what is my personal mail ID?") is True
    assert is_private_data_query("show me my transaction history") is True
    
    # Public queries
    assert is_private_data_query("what is GST?") is False
    assert is_private_data_query("how to settle a PF claim online?") is False
    assert is_private_data_query("what is the capital of India?") is False
    assert is_private_data_query("general registration process for tax center") is False

def test_intent_classification_category():
    """Verify that intent classifier properly assigns query category."""
    # We bypass LLM invocation if it's rule-based chitchat under 15 characters,
    # so we use longer queries for LLM classification.
    packet_business = classify_intent("How many clients do I currently have in my business and what are their names?")
    assert packet_business.query_category == "BUSINESS_INFORMATION"
    
    packet_public = classify_intent("What is GST and how does it affect small shopkeepers in India?")
    assert packet_public.query_category == "PUBLIC_INFORMATION"

def test_web_search_bypass_for_private_queries():
    """Verify that web search is bypassed/blocked for private queries."""
    res = web_search("mere business me kitne clients hain aur unke naam batao")
    assert res == REFUSAL_PRIVATE_DATA

def test_worker_refusal_on_empty_context():
    """Verify that worker refuses to answer when local context is empty for private data queries."""
    task = TaskDTO(
        task_id="T1",
        objective="Find the names and counts of clients in Shubham's tax consultancy business",
        department="information",
        depends_on=[],
        priority=1,
        context={
            "intent_packet": {
                "query_category": "BUSINESS_INFORMATION"
            }
        }
    )
    # Empty scoped context (database details/clients are missing)
    scoped_context = {
        "objective": task.objective,
        "web_search": REFUSAL_PRIVATE_DATA,
        "profile_slice": {}
    }
    
    # Execute the worker
    result, _ = run_worker(task, scoped_context, llm_dept)
    
    # The worker must output a strict refusal
    assert "Mere paas aapke actual client records ka access nahi hai" in result or REFUSAL_PRIVATE_DATA in result

def test_auditor_validation_on_fabricated_claims():
    """Verify that post-execution auditor fails tasks with fabricated info not present in context."""
    validator = PostExecutionValidator(llm=llm_dept)
    
    task = TaskDTO(
        task_id="T1",
        objective="Find client names for Shubham Swarnkar's business",
        department="information",
        depends_on=[],
        priority=1,
        context={
            "intent_packet": {
                "query_category": "BUSINESS_INFORMATION"
            },
            # Store the scoped context that was passed to the worker
            "scoped_context": {
                "objective": "Find client names",
                "profile_slice": {
                    "business_context": {
                        "business_name": "Anshu Computer & Tax Consultancy"
                    }
                },
                "web_search": REFUSAL_PRIVATE_DATA
            }
        }
    )
    
    # Fabricated result claiming specific client names not in context
    fabricated_result = "Here are your clients: Ramesh Kumar, Suresh Gupta, and Anita Sharma."
    
    passed, reason = validator.audit(task, fabricated_result)
    assert passed is False
    assert "Source Authority Violation" in reason or "fabricated" in reason.lower()
    
    # Valid refusal result
    refusal_result = "Mere paas aapke actual client records ka access nahi hai."
    passed_refusal, reason_refusal = validator.audit(task, refusal_result)
    assert passed_refusal is True

def test_negative_evidence():
    """Verify that querying for clients with zero evidence/empty context returns refusal."""
    validator = PostExecutionValidator(llm=llm_dept)
    
    task = TaskDTO(
        task_id="T2",
        objective="Mere top 10 clients batao",
        department="information",
        depends_on=[],
        priority=1,
        context={
            "intent_packet": {
                "query_category": "BUSINESS_INFORMATION"
            },
            "scoped_context": {
                "objective": "Mere top 10 clients batao",
                "sources": {
                    "AUTHORITY_MEMORY": {},
                    "AUTHORITY_DATABASE": {}
                }
            }
        }
    )
    
    # 1. Hallucinated output listing clients must fail the deterministic check
    fabricated_output = "Aapke top 10 clients hain: Rohan, Rajesh, Priya, Neha, Pooja, Amit, Vicky, Ajay, Rahul aur Vikram."
    passed, reason = validator.audit(task, fabricated_output)
    assert passed is False
    assert "Source Authority Violation" in reason

    # 2. Refusal output must pass
    refusal_output = REFUSAL_PRIVATE_DATA
    passed_refusal, _ = validator.audit(task, refusal_output)
    assert passed_refusal is True

def test_partial_evidence():
    """Verify that querying for clients with partial context returns only known evidence and fails on hallucinated extras."""
    validator = PostExecutionValidator(llm=llm_dept)
    
    task = TaskDTO(
        task_id="T3",
        objective="Mere top 10 clients batao",
        department="information",
        depends_on=[],
        priority=1,
        context={
            "intent_packet": {
                "query_category": "BUSINESS_INFORMATION"
            },
            "scoped_context": {
                "objective": "Mere top 10 clients batao",
                "sources": {
                    "AUTHORITY_MEMORY": {
                        "clients": ["Ram", "Shyam"]
                    }
                }
            }
        }
    )
    
    # 1. Output containing ONLY the known clients must pass
    valid_output = "Aapke records ke hisab se, aapke clients hain: Ram aur Shyam."
    passed_valid, _ = validator.audit(task, valid_output)
    assert passed_valid is True

    # 2. Output containing fabricated clients (Mohan, Rohan) must fail
    fabricated_output = "Aapke top clients hain: Ram, Shyam, Mohan aur Rohan."
    passed_fab, reason_fab = validator.audit(task, fabricated_output)
    assert passed_fab is False
    assert "Source Authority Violation" in reason_fab
    assert "Mohan" in reason_fab or "Rohan" in reason_fab
