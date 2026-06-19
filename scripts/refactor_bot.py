import os
import re

babu_dir = os.path.join(r"c:\Users\LENOVO\.gemini\antigravity\scratch\Aria", "babu")
bot_path = os.path.join(babu_dir, "bot.py")

with open(bot_path, "r", encoding="utf-8") as f:
    content = f.read()

# We need to extract the functions: init_postgres_db, init_durable_checkpoint_db, cleanup_corrupt_failures
# and the __main__ block.

def extract_block(text, start_pattern, next_def_pattern):
    # Finds a block starting with start_pattern and ending right before next_def_pattern or EOF
    match_start = re.search(start_pattern, text)
    if not match_start:
        return text, ""
    
    start_idx = match_start.start()
    
    # Find next def or if __name__ after start_idx
    match_end = re.search(next_def_pattern, text[start_idx + len(match_start.group(0)):])
    if match_end:
        end_idx = start_idx + len(match_start.group(0)) + match_end.start()
        # look backward to remove trailing newlines
        block = text[start_idx:end_idx]
        new_text = text[:start_idx] + text[end_idx:]
        return new_text, block
    else:
        block = text[start_idx:]
        new_text = text[:start_idx]
        return new_text, block

new_content = content
blocks = {}

# Extract init_postgres_db
new_content, b1 = extract_block(new_content, r"^def init_postgres_db\(\):", r"^(?:if DATABASE_URL|def )")
blocks["init_postgres_db"] = b1

# Extract if DATABASE_URL...
new_content, b1_call = extract_block(new_content, r"^if DATABASE_URL and \(DATABASE_URL\.startswith\(\"postgres://\"\).*:\n    init_postgres_db\(\)", r"^def ")
blocks["init_pg_call"] = b1_call

# Extract init_durable_checkpoint_db
new_content, b2 = extract_block(new_content, r"^def init_durable_checkpoint_db\(\):", r"^def ")
blocks["init_durable_checkpoint_db"] = b2

# Extract cleanup_corrupt_failures
new_content, b3 = extract_block(new_content, r"^def cleanup_corrupt_failures\(\):", r"^if __name__ == \"__main__\":")
blocks["cleanup_corrupt_failures"] = b3

# Extract __main__
new_content, b4 = extract_block(new_content, r"^if __name__ == \"__main__\":", r"\Z")
blocks["main_block"] = b4

# Now save the blocks to bootstrap.py
bootstrap_code = f"""import os
import sys
import json
import sqlite3
import threading
import time
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler

{blocks['init_postgres_db']}

{blocks['init_durable_checkpoint_db']}

{blocks['cleanup_corrupt_failures']}

def bootstrap_brain():
    print("[BOOTSTRAP] Phase 0: Load Constitution", flush=True)
    print("[BOOTSTRAP] Phase 1: Load ROOT_INDEX", flush=True)
    print("[BOOTSTRAP] Phase 2: Load Governance", flush=True)
    
    print("[BOOTSTRAP] Phase 3: Load Configuration", flush=True)
    from .bot import (
        DATABASE_URL, DB_PATH, start_health_server, is_google_configured,
        TELEGRAM_TOKEN, start_social_scheduler, cmd_launch, cmd_clear,
        cmd_goals, cmd_help, cmd_stats, cmd_model, cmd_postnow, cmd_promote,
        cmd_retire, on_message, on_post_callback, telegram_error_handler
    )
    import babu.bot as bot_module

    print("[BOOTSTRAP] Phase 4: Verify Infrastructure", flush=True)
    if DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
        init_postgres_db()
    
    # We call init_durable_checkpoint_db and ignore the connection return for bootstrap
    init_durable_checkpoint_db()
    
    cleanup_corrupt_failures()

    print("[BOOTSTRAP] Phase 5: Load Brain", flush=True)
    print("[BOOTSTRAP] Phase 6: Initialize Awareness", flush=True)
    print("[BOOTSTRAP] Phase 7: Compile Planner", flush=True)
    
    print("[BOOTSTRAP] Phase 8: Open Transports", flush=True)
    
    health_thread = threading.Thread(target=start_health_server, name="web_dashboard_health_server", daemon=True)
    health_thread.start()
    
    google_status = f"Google Workspace ({{'active' if is_google_configured() else 'NOT configured'}})"
    print(f"--- ARIA IS LIVE | Memory | Web Search | Knowledge Base | {{google_status}} | Unified Swarm ---", flush=True)
    
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot_module.tg_application = bot
    
    start_social_scheduler(bot)
    
    bot.add_handler(CommandHandler("launch", cmd_launch))
    bot.add_handler(CommandHandler("clear",  cmd_clear))
    bot.add_handler(CommandHandler("goals",  cmd_goals))
    bot.add_handler(CommandHandler("help",   cmd_help))
    bot.add_handler(CommandHandler("stats",  cmd_stats))
    bot.add_handler(CommandHandler("model",  cmd_model))
    bot.add_handler(CommandHandler("postnow", cmd_postnow))
    bot.add_handler(CommandHandler("promote", cmd_promote))
    bot.add_handler(CommandHandler("retire", cmd_retire))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE | filters.Document.ALL) & (~filters.COMMAND), on_message))
    bot.add_handler(CallbackQueryHandler(on_post_callback))
    bot.add_error_handler(telegram_error_handler)
    
    while True:
        try:
            bot.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            print(f"[TELEGRAM ERROR] Polling failed or conflicted: {{e}}. Retrying in 15 seconds...", flush=True)
            time.sleep(15)
"""

# write new_content back to bot.py
new_content += "\n\nif __name__ == '__main__':\n    from .bootstrap import bootstrap_brain\n    bootstrap_brain()\n"

with open(bot_path, "w", encoding="utf-8") as f:
    f.write(new_content)

bootstrap_path = os.path.join(babu_dir, "bootstrap.py")
with open(bootstrap_path, "w", encoding="utf-8") as f:
    f.write(bootstrap_code)

print("Refactoring done.")
