import os
import threading
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_all")

def start_telegram_bot():
    logger.info("Starting Telegram Bot...")
    # Assuming bot.py or main.py starts the bot. Let's run it via system command.
    os.system("python bot.py")

def start_web_api():
    logger.info("Starting Web API on port $PORT or 8000...")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web_api:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    # Start telegram bot in a background thread
    tg_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    tg_thread.start()
    
    # Start web API on the main thread
    start_web_api()
