import re

def main():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Find where the open roles heading starts
    match = re.search(r'Open Roles', html, re.IGNORECASE)
    if match:
        print("Found 'Open Roles' at index:", match.start())
        start = max(0, match.start() - 500)
        end = min(len(html), match.start() + 4000)
        print("--- Context around Open Roles ---")
        print(html[start:end])
    else:
        print("'Open Roles' not found in HTML!")

if __name__ == "__main__":
    main()
