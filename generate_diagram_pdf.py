import os
import sys
from fpdf import FPDF

IMAGE_PATH = r"C:\Users\LENOVO\.gemini\antigravity\brain\6d0c89f4-8e58-41cb-9549-ca0575e8a3a5\babu_flow_diagram_1780467365626.png"
OUTPUT_PATH = r"c:\Users\LENOVO\.gemini\antigravity\scratch\Babu\ARIA_Architecture_and_Flow.pdf"

class PDF(FPDF):
    def header(self):
        # Draw header on all pages except maybe cover or if skipped
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(30, 41, 59) # Slate-800
        self.cell(0, 10, "ARIA Cognitive OS", align="L")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(100, 116, 139) # Slate-500
        self.cell(0, 10, "Architecture & Information Flow Diagram", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(226, 232, 240) # Slate-200
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184) # Slate-400
        self.cell(0, 10, f"ARIA Architecture Documentation - Page {self.page_no()}", align="C")

    def section_title(self, title):
        self.ln(4)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(15, 23, 42) # Slate-900
        self.set_fill_color(241, 245, 249) # Slate-100
        self.cell(0, 8, "  " + title, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(71, 85, 105) # Slate-600
        self.multi_cell(0, 5, text)
        self.ln(2)

    def mono(self, text):
        self.set_font("Courier", "", 9)
        self.set_text_color(15, 23, 42)
        self.set_fill_color(248, 250, 252)
        self.multi_cell(0, 5, text, fill=True)
        self.ln(2)

def generate_pdf():
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Page 1: Introduction and Pipeline Mechanics
    pdf.add_page()
    
    pdf.section_title("1. Architectural Overview")
    pdf.body(
        "ARIA is built around a dynamic task-decomposition and execution framework designed to "
        "reconcile open-ended user inquiries with highly deterministic tool invocation and "
        "rigorous compliance checks. Rather than executing a simple linear prompt chain, ARIA "
        "compiles a directed acyclic graph (DAG) of specialized subtasks tailored to the user's specific intent."
    )
    
    pdf.section_title("2. Pipeline Stages & Information Flow")
    pdf.body(
        "The core runtime cycle is structured as follows:\n\n"
        "1. INGESTION & TRIAGING: Telegram user input is intercepted. The intent router analyzes "
        "the query's complexity, history context, and explicit command overrides (such as /walk or /sprint) "
        "to assign the request to the appropriate conversational Gear.\n\n"
        "2. GOAL DECOMPOSITION: If SPRINT or LAUNCH gear is activated, the Strategic Planner "
        "analyzes the request alongside historical failures and active anti-pattern guidelines, "
        "decomposing the query into a structured GoalGraph comprising individual TaskDTO elements.\n\n"
        "3. ORCHESTRATION KERNEL: The TaskEngine schedules ready tasks, validating that dependencies "
        "are satisfied before dispatching them. It propagates results through the DAG and handles failsafes.\n\n"
        "4. MIDDLE MANAGEMENT (DEPARTMENT HEADS): Tasks are routed to specialized Department Heads "
        "(Research, Analysis, Writing, Execution, PA). The heads validate inputs, extract narrow context, "
        "invoke workers or programmatic APIs, and compress results to prevent context bloat.\n\n"
        "5. AUDITING & COMPLIANCE: Before any task runs and after it finishes, the Auditor (Pre and Post "
        "validators) checks the task objectives and outputs against safety guidelines and failures logs."
    )
    
    # Page 2: Visual Diagram Page
    pdf.add_page()
    pdf.section_title("3. System Architecture Diagram")
    pdf.body(
        "The diagram below illustrates the exact execution pipeline, state machine, and data flow. "
        "It details the separation between direct execution paths and database/memory read-write points."
    )
    
    # Place image in the center of the page
    # Page width is 210mm. Margins are 10mm left and right, leaving 190mm.
    # Page height is 297mm.
    if os.path.exists(IMAGE_PATH):
        # Place the image and scale it to fit the page width
        pdf.image(IMAGE_PATH, x=10, y=pdf.get_y() + 5, w=190)
    else:
        pdf.body("[IMAGE ERROR] Flow diagram image file was not found.")
        
    # Page 3: Memory and Database Integration
    pdf.add_page()
    pdf.section_title("4. Memory & Database Callpoints")
    pdf.body(
        "Memory inside ARIA is split into three separate scopes to balance execution speed, "
        "durability, and cognitive self-correction:\n\n"
        "1. SESSION CHECKPOINTS (SQLite / Supabase Postgres):\n"
        "State graph session memory is written at every node transition. This ensures that in-progress "
        "conversational states are not lost if the container restarts. The connection automatically "
        "ports from local SQLite to Supabase if the DATABASE_URL is configured in the environment.\n\n"
        "2. SYSTEM MEMORY & LEDGER (SQLite / Supabase Postgres):\n"
        "ARIA writes telemetry records (prompt, completion, and total tokens used), goals, and "
        "detailed execution metrics for every task. These database operations are offloaded to "
        "asynchronous background threads to keep latency at zero for the end user.\n\n"
        "3. EPISTEMIC IMMUNE SYSTEM (failures.json):\n"
        "When the Auditor detects a validation failure (e.g. missing citations, formatting errors, or "
        "incorrect tools usage), it logs the specific failure pattern into failures.json. "
        "On subsequent runs, the Strategic Planner loads these anti-patterns and adjusts task objectives "
        "and checklists dynamically to prevent repeating past errors."
    )
    
    pdf.section_title("5. Taxonomic Separation: Research vs. Information")
    pdf.body(
        "To optimize API rate-limits and token latency, the system separates standard search queries "
        "from academic-level research:\n"
        "- Information Department: Used for general lookups, web searches, and profile fetches. Uses "
        "lightweight workers and bypasses strict auditor citation checks.\n"
        "- Research Department: Reserved for deep research requests. Mobilizes the full Analyst-Skeptic-Strategist "
        "swarm and enforces rigorous academic-level verification gates."
    )
    
    pdf.output(OUTPUT_PATH)
    print(f"PDF generated successfully at {OUTPUT_PATH}")

if __name__ == "__main__":
    generate_pdf()
