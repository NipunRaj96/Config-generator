import sqlite3
import os

PROJECT_ROOT = r"c:\Users\nipun.kumar\Projects\config"
DB_PATH = os.path.join(PROJECT_ROOT, "knowledge_base", "config_cache.db")

print(f"Connecting to database at: {DB_PATH}")
if not os.path.exists(DB_PATH):
    print("Database file does not exist!")
    exit(1)

domains = ["www.isisecurity.in", "www.webcooks.in"]

with sqlite3.connect(DB_PATH) as conn:
    cursor = conn.cursor()
    for d in domains:
        print(f"Deleting cached entry for domain: {d}")
        cursor.execute("DELETE FROM config_cache WHERE domain = ?", (d,))
    conn.commit()
    print("Database changes committed successfully.")
