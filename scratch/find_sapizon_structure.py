import re

with open("scratch/sapizon_rendered.html", "r", encoding="utf-8") as f:
    html_content = f.read()

print(f"HTML Length: {len(html_content)}")

# Find position of Sales Consultant
pos = html_content.find("Sales Consultant")
if pos != -1:
    print("Found Sales Consultant!")
    snippet = html_content[pos-300:pos+1200]
    print("--- SNIPPET ---")
    print(snippet)
    print("----------------")
else:
    print("Sales Consultant not found!")

# Let's test the ground truth regex
# (?s)Position[^>]+>(([^<]+))[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>(.+?)[\"']Apply
gt_pattern = r"(?s)Position[^>]+>(([^<]+))[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>(.+?)[\"']Apply"
compiled = re.compile(gt_pattern)
matches = compiled.findall(html_content)
print(f"Ground Truth Regex matches: {len(matches)}")
if matches:
    print("First match details:")
    print(matches[0])
