import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    html = f.read()

# Strip script and style blocks first
cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)

# Trimming logic
first_match = cleaned.find("awsm-job-listing")
start = max(0, first_match - 1500)
snippet = cleaned[start : start + 12000]

with open("scratch/cloudsmartz_snippet.txt", "w", encoding="utf-8") as f:
    f.write(snippet)

print("Dumped snippet to scratch/cloudsmartz_snippet.txt")
