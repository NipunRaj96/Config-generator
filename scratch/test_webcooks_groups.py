import re

with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's clean the HTML exactly like the generator does
html = re.sub(r'(?s)<!--.*?-->', '', html)
html = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', html)

# Let's try matching with a nested regex
pattern = r'(?s)<div[^>]*class="group p-6 rounded-3xl bg-white[^"]*">.*?<h3[^>]*>((([^<]+)))</h3>.*?(?:text-blue-500 text-sm|class="flex items-center)[^>]*>.*?svg[^>]*>.*?</svg>\s*([^<]+?)\s*</div>'

try:
    compiled = re.compile(pattern)
    matches = compiled.findall(html)
    print(f"Total matches: {len(matches)}")
    for i, m in enumerate(matches[:3]):
        print(f"Match {i+1}:")
        print(f"  Group 1 (JOBLINK):   {repr(m[0])}")
        print(f"  Group 2 (JOBID):     {repr(m[1])}")
        print(f"  Group 3 (JOBTITLE):  {repr(m[2])}")
        print(f"  Group 4 (JOBDESC):   {repr(m[3].strip())}")
except Exception as e:
    print("Error:", e)
