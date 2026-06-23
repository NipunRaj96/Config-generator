import sqlite3
import json
import os

db_path = "knowledge_base/config_cache.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print("Tables:", tables)
        
        for table in tables:
            tname = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {tname};")
            count = cursor.fetchone()[0]
            print(f"Table {tname}: {count} rows")
            
            # Print rows
            cursor.execute(f"SELECT * FROM {tname} LIMIT 5;")
            rows = cursor.fetchall()
            for r in rows:
                print(r)
    except Exception as e:
        print(f"Error querying db: {e}")
    finally:
        conn.close()
else:
    print(f"Db path does not exist: {db_path}")
