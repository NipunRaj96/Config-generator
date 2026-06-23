import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.config import CACHE_DB_PATH

def main():
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    domains = ['www.isisecurity.in', 'www.webcooks.in', 'www.detailingdevils.com']
    
    for d in domains:
        cursor.execute("DELETE FROM config_cache WHERE domain = ?", (d,))
        print(f"Cleared cache for {d}")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
