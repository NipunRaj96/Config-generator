import re
import json

def run():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Search for headings
    headings = re.findall(r'<h[1-4][^>]*>(.*?)</h[1-4]>', html, re.DOTALL)
    print(f"Found {len(headings)} headings in HTML:")
    for h in headings[:20]:
        print("  -", re.sub(r'<[^>]*>', '', h).strip())
        
    # Search for __NEXT_DATA__
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        print("\nFound __NEXT_DATA__ JSON script block!")
        try:
            data = json.loads(match.group(1))
            # Save the JSON block to inspect
            with open("scratch/isi_next_data.json", "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2)
            print("Saved __NEXT_DATA__ JSON to scratch/isi_next_data.json")
            
            # Print a quick summary of keys in props
            props = data.get("props", {})
            page_props = props.get("pageProps", {})
            print("Props keys:", props.keys())
            print("PageProps keys:", page_props.keys())
            if "jobs" in page_props:
                print("Found 'jobs' in pageProps! Count:", len(page_props["jobs"]))
                for j in page_props["jobs"][:3]:
                    print("  -", j)
            elif "dehydratedState" in page_props:
                print("Found 'dehydratedState' in pageProps!")
                queries = page_props["dehydratedState"].get("queries", [])
                for idx, q in enumerate(queries):
                    state = q.get("state", {})
                    data_inner = state.get("data", {})
                    print(f"  Query {idx} state data type: {type(data_inner)}")
                    if isinstance(data_inner, dict):
                        print(f"    keys: {list(data_inner.keys())}")
                        # Check if any list in data_inner
                        for k, v in data_inner.items():
                            if isinstance(v, list) and v:
                                print(f"    - list key '{k}' count: {len(v)}")
                                for item in v[:2]:
                                    print(f"      item: {str(item)[:200]}")
        except Exception as e:
            print("Error parsing __NEXT_DATA__:", e)

if __name__ == "__main__":
    run()
