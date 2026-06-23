with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)
cleaned = re.sub(r'(?s)<!--.*?-->', '', cleaned)
cleaned = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', cleaned)

print(repr(cleaned[5648 - 100 : 5648 + 300]))
