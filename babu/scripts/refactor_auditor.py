import os
import re

file_path = r'D:\Aria\babu\auditor.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace class docstring
content = content.replace(
    '"""Semantic validation and hallucination checks. Executes after worker completes."""',
    '"""EGI Axiom U1 (Audit Non-Cognitive): Structural validation only. Executes after worker completes."""'
)

# Replace LLM prompt section
old_llm_section = '''        # 3. Semantic and Hallucination audit using LLM (if provided)
        if self.llm:
            print(f"[AUDITOR:POST] Initiating semantic validator for task '{task.task_id}'...", flush=True)
            
            checklist_str = ""
            if task.compliance_checklist:
                checklist_str = "\\n".join(f"- [ ] {item}" for item in task.compliance_checklist)
            else:
                checklist_str = "- [ ] Verify that the worker actually answered/accomplished the objective.\\n- [ ] Check for factual truthfulness and style alignment."

            system_prompt = (
                "You are BABU's Post-Execution Auditor and Risk Assessor. Your job is to audit a worker's output "
                "for structural validity, factual truthfulness, compliance with the checklist, and overall operational risk.\\n"
                "You must verify each checklist item individually. Rather than simply blocking, assess the risk.\\n"
                "Do NOT fail entire workflows because a research or information retrieval task has low confidence or lacks detailed academic citations, as long as it has retrieved some correct and relevant details.\\n"
                "If the task belongs to 'research' or 'information' departments, assign a lower confidence score and flag uncertainty, but set passed to true when it is safe to continue.\\n\\n"
                "COMPLIANCE CHECKLIST:\\n"
                f"{checklist_str}\\n\\n"
                "CRITICAL AUDITING GATES:\\n"
                "- Verify that the worker actually answered/accomplished the objective.\\n"
                "- Check for hallucinated success markers (e.g. claiming an action was executed when it was not).\\n"
                "- Check if the output claims the model is 'flawless', 'perfect', or '100% correct'.\\n"
                "- Be fair, realistic, and constructive. Do NOT reject or block valid responses simply because they are concise, summarizing, or convey upstream results clearly, as long as they address the objective.\\n"
                "- For local profile searches, personal details lookup, or simple information retrievals, do NOT penalize the worker for lacking academic web citations or complex external evidence. The local user profile or local context is the authoritative source. If the worker presents the correct information retrieved from the local profile, treat it as fully compliant and verified.\\n"
                "- For the 'information' department (designed for general information retrieval, simple web search, and Wikipedia-style lookups), do NOT penalize the worker for lacking academic-level citations, sources, or strict evidence links, unless the task objective or checklist explicitly demands them. The 'information' department only requires retrieving accurate facts or answers concisely and factually.\\n"
            )'''

new_llm_section = '''        # 3. Structural checklist audit using LLM (if provided) - EGI Axiom U1 Enforcement
        if self.llm:
            print(f"[AUDITOR:POST] Initiating structural validator for task '{task.task_id}'...", flush=True)
            
            checklist_str = ""
            if task.compliance_checklist:
                checklist_str = "\\n".join(f"- [ ] {item}" for item in task.compliance_checklist)
            else:
                checklist_str = "- [ ] Verify that the worker actually answered/accomplished the objective."

            system_prompt = (
                "You are BABU's Post-Execution Auditor. According to EGI Axiom U1 (Audit Non-Cognitive), your job is strictly to audit a worker's output "
                "for structural validity and compliance with the checklist. You MUST NOT evaluate factual truthfulness or beliefs.\\n"
                "Your ONLY task is to check if the output matches the explicit checklist constraints and does not hallucinate success markers.\\n"
                "You must verify each checklist item individually as a binary (Pass/Fail) check.\\n"
                "Do NOT fail workflows based on your own internal knowledge of facts. Only evaluate against the provided checklist.\\n\\n"
                "COMPLIANCE CHECKLIST:\\n"
                f"{checklist_str}\\n\\n"
                "CRITICAL AUDITING GATES:\\n"
                "- Verify that the worker actually answered/accomplished the objective.\\n"
                "- Check for hallucinated success markers (e.g. claiming an action was executed when it was not).\\n"
                "- Check if the output claims the model is 'flawless', 'perfect', or '100% correct'.\\n"
                "- Be fair and realistic. Do NOT reject or block valid responses simply because they are concise.\\n"
            )'''

# Simple string replace
content = content.replace(old_llm_section, new_llm_section)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
