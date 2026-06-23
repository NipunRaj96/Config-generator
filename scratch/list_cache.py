import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.config import CACHE_DB_PATH

def main():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT domain, tech_status, site_type, crawler_type, created_at FROM config_cache")
    rows = cursor.fetchall()
    print(f"Total cache entries: {len(rows)}")
    for r in rows:
        print(f"Domain: {r['domain']}, Status: {r['tech_status']}, SiteType: {r['site_type']}, CrawlerType: {r['crawler_type']}, Created: {r['created_at']}")
        
    conn.close()

if __name__ == "__main__":
    main()
