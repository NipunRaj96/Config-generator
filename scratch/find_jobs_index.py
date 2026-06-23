with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

cleaned = html
import re
cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', cleaned)
cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)

pos = cleaned.find("FrontEnd Development")
print(f"FrontEnd Development position: {pos} out of {len(cleaned)}")
print(f"Current Openings position: {cleaned.find('Current Openings')}")
