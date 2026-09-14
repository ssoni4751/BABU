import os
import re

file_path = r'D:\Aria\babu\bot.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

func_code = '''
def generate_contextual_minimum(session_id: str):
    \"\"\"
    EGI Axiom T1 (Transition Contract): Generates a 'Contextual Minimum' summary 
    of the sealed epoch to prevent implicit Temporal Shadowing in the next epoch.
    \"\"\"
    global _histories
    with _memory_lock:
        h = list(_histories[session_id])
        if not h:
            return
            
    # Normally we'd use the LLM to summarize h, but to avoid blocking DB transactions
    # and latency, we create a structured handoff note.
    # In a full LLM implementation, we would call Groq here.
    summary_text = "[CONTEXTUAL MINIMUM HANDOFF]\\n"
    summary_text += "The previous epoch was sealed. Key interactions:\\n"
    for msg in h[-3:]: # Take the last 3 exchanges as the contextual minimum
        role = msg.get("role", "user")
        text = msg.get("content", "")[:100] # Truncated
        summary_text += f"- {role}: {text}...\\n"
        
    with _memory_lock:
        _histories[session_id].clear()
        _histories[session_id].append({"role": "system", "content": summary_text})
        print(f"[GOVERNANCE] Contextual Minimum generated for session '{session_id}'.", flush=True)

'''

# Inject function before seal_epoch
if 'def generate_contextual_minimum' not in content:
    content = content.replace('def seal_epoch(epoch_id: str):', func_code + '\ndef seal_epoch(epoch_id: str):')

# Update the call site where seal_epoch is called
call_site = '''            t_seal_start = time.time()
            seal_epoch(epoch_id)
            t_seal_duration = round(time.time() - t_seal_start, 4)'''

new_call_site = '''            t_seal_start = time.time()
            seal_epoch(epoch_id)
            generate_contextual_minimum(session_id)
            t_seal_duration = round(time.time() - t_seal_start, 4)'''

if 'generate_contextual_minimum(session_id)' not in content:
    content = content.replace(call_site, new_call_site)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
