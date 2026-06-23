import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.config import CACHE_DB_PATH

def main():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    domains = ['isisecurity.in', 'webcooks.in', 'detailingdevils.com']
    
    print("--- Config Cache Entries ---")
    for domain in domains:
        cursor.execute("SELECT * FROM config_cache WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row:
            print(f"Domain: {domain}")
            for k in row.keys():
                if k == 'config_json' and row[k]:
                    print(f"  {k}: {row[k][:200]}...")
                else:
                    print(f"  {k}: {row[k]}")
        else:
            print(f"Domain: {domain} NOT FOUND in cache.")
        print("-" * 50)
        
    conn.close()

if __name__ == "__main__":
    main()
