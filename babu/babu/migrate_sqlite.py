import os, sqlite3, sys

# Path to the BABU checkpoint SQLite DB
base_dir = os.path.dirname(os.path.abspath(r'C:\Users\LENOVO\.gemini\antigravity\scratch\Babu\babu\bot.py'))
db_path = os.path.join(base_dir, 'memory', 'babu_checkpoint.db')

if not os.path.exists(db_path):
    print('SQLite DB not found at', db_path)
    sys.exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
# Add missing columns if they do not exist (SQLite ignores if already present)
cur.execute('ALTER TABLE trusted_templates ADD COLUMN IF NOT EXISTS average_execution_time REAL DEFAULT 0.0')
cur.execute('ALTER TABLE trusted_templates ADD COLUMN IF NOT EXISTS average_token_cost REAL DEFAULT 0.0')
cur.execute('ALTER TABLE trusted_templates ADD COLUMN IF NOT EXISTS status TEXT DEFAULT "ACTIVE"')
conn.commit()
conn.close()
print('Migration applied to SQLite DB')
