import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    html_content = f.read()

# Ground truth regex:
gt_regex = r"(?s)Posting_Title.+?;.+?;([^&]+).+?City.+?;.+?;([^&]+).+?Work_Experience.+?;.+?;([^&]+).+?id&.+?;.+?;(([^&]+))"

matches = re.findall(gt_regex, html_content)
print(f"Ground truth regex matches: {len(matches)}")
if matches:
    print("First match:", matches[0])
else:
    # Let's search for the first 1000 characters of the URL query string format
    # maybe it matches a Zoho URL query string in a script or link?
    print("No matches. Let's see if there is any other occurrence of Posting_Title in the html.")
    # Search for Posting_Title followed by any characters
    sample_matches = re.findall(r"Posting_Title[^\n]{1,200}", html_content)
    print("Some Posting_Title lines found:")
    for m in sample_matches[:5]:
        print(m)
