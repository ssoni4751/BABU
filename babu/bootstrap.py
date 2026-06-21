import os
import time
import threading
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler

def bootstrap_brain():
    print("[BOOTSTRAP] Phase 0: Load Constitution", flush=True)
    brain_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain")
    const_path = os.path.join(brain_dir, "constitution.md")
    if os.path.exists(const_path):
        with open(const_path, "r", encoding="utf-8") as f:
            const_lines = f.readlines()
        print(f"  -> Constitution loaded successfully ({len(const_lines)} lines) from {const_path}", flush=True)
    else:
        raise RuntimeError(f"Critical Boot Failure: Constitution file not found at {const_path}")
        
    try:
        from babu.governance import CONSTITUTION_PATH as e0_const_path, E0_A_Rules
        print(f"  -> E0 programmatic rules verified from {e0_const_path} ({len(E0_A_Rules)} rules)", flush=True)
    except Exception as e:
        print(f"  -> [WARNING] E0 governance rules verification failed: {e}", flush=True)
    
    print("[BOOTSTRAP] Phase 1: Load Configuration", flush=True)
    import babu.bot as bot_module
    token_status = "set" if bot_module.TELEGRAM_TOKEN else "MISSING"
    db_status = "set" if bot_module.DATABASE_URL else "sqlite (default)"
    print(f"  -> Env config: TELEGRAM_TOKEN={token_status}, DATABASE_URL={db_status}", flush=True)
    
    print("[BOOTSTRAP] Phase 2: Verify Infrastructure", flush=True)
    if bot_module.DATABASE_URL and (bot_module.DATABASE_URL.startswith("postgres://") or bot_module.DATABASE_URL.startswith("postgresql://")):
        print("  -> Initializing PostgreSQL DB...", flush=True)
        bot_module.init_postgres_db()
    else:
        print("  -> PostgreSQL DATABASE_URL not set; using SQLite.", flush=True)
    
    print("  -> Initializing SQLite Durable Checkpoint DB...", flush=True)
    bot_module.init_durable_checkpoint_db()
    print("  -> Cleaning up corrupt failures...", flush=True)
    bot_module.cleanup_corrupt_failures()

    print("[BOOTSTRAP] Phase 3: Load Brain Index", flush=True)
    for name in ["organization.md", "doctrine.md", "capabilities.md"]:
        p = os.path.join(brain_dir, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            print(f"  -> {name} loaded successfully ({len(content)} bytes)", flush=True)
        else:
            raise RuntimeError(f"Critical Boot Failure: Brain Index component {name} not found at {p}")
    
    print("[BOOTSTRAP] Phase 4: Compile Planner", flush=True)
    try:
        from babu.planner import compile_planner
        compiled_ctx = compile_planner()
        if compiled_ctx:
            print(f"  -> Planner context compiled successfully ({len(compiled_ctx)} characters)", flush=True)
        else:
            print("  -> [WARNING] Planner context compilation returned empty text.", flush=True)
    except Exception as e:
        print(f"  -> [WARNING] Planner compilation failed: {e}", flush=True)
    
    print("[BOOTSTRAP] Phase 5: Start Transports", flush=True)
    
    health_thread = threading.Thread(target=bot_module.start_health_server, name="web_dashboard_health_server", daemon=True)
    health_thread.start()
    
    google_status = f"Google Workspace ({'active' if bot_module.is_google_configured() else 'NOT configured'})"
    print(f"--- BABU IS LIVE | Memory | Web Search | Knowledge Base | {google_status} | Unified Swarm ---", flush=True)
    
    bot = ApplicationBuilder().token(bot_module.TELEGRAM_TOKEN).build()
    bot_module.tg_application = bot
    
    bot_module.start_social_scheduler(bot)
    
    bot.add_handler(CommandHandler("launch", bot_module.cmd_launch))
    bot.add_handler(CommandHandler("clear",  bot_module.cmd_clear))
    bot.add_handler(CommandHandler("goals",  bot_module.cmd_goals))
    bot.add_handler(CommandHandler("help",   bot_module.cmd_help))
    bot.add_handler(CommandHandler("stats",  bot_module.cmd_stats))
    bot.add_handler(CommandHandler("model",  bot_module.cmd_model))
    bot.add_handler(CommandHandler("postnow", bot_module.cmd_postnow))
    bot.add_handler(CommandHandler("promote", bot_module.cmd_promote))
    bot.add_handler(CommandHandler("retire", bot_module.cmd_retire))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE | filters.Document.ALL) & (~filters.COMMAND), bot_module.on_message))
    bot.add_handler(CallbackQueryHandler(bot_module.on_post_callback))
    bot.add_error_handler(bot_module.telegram_error_handler)
    
    while True:
        try:
            bot.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            print(f"[TELEGRAM ERROR] Polling failed or conflicted: {e}. Retrying in 15 seconds...", flush=True)
            time.sleep(15)
if __name__ == "__main__":
    bootstrap_brain()
