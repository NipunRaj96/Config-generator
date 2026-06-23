import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from src.locrgx_generator import LOCRGXGenerator

def test_clean(raw_json_str):
    print("Raw JSON String:")
    print(repr(raw_json_str))
    cleaned = LOCRGXGenerator._clean_json_regex_escapes(raw_json_str)
    print("Cleaned JSON String:")
    print(repr(cleaned))
    try:
        parsed = json.loads(cleaned)
        print("Parsed locrgx:")
        print(repr(parsed["locrgx"]))
    except Exception as e:
        print("Parse Error:", e)

# Scenario 1: LLM returns properly escaped JSON
raw1 = '{\n  "locrgx": "(?s)<div[^>]*class=\\"[^\\\"\\\\s]*flex flex-col gap-4 p-6 rounded-2xl bg-white shadow-lg border border-gray\\"",\n  "locrgxseq": "JOBTITLE",\n  "move_to_jd": 0\n}'
test_clean(raw1)

# Scenario 2: LLM returns double-escaped or poorly escaped quotes
raw2 = '{\n  "locrgx": "(?s)<div[^>]*class=\\"[^\\"\\\\s]*flex flex-col gap-4 p-6 rounded-2xl bg-white shadow-lg border border-gray\\"",\n  "locrgxseq": "JOBTITLE",\n  "move_to_jd": 0\n}'
test_clean(raw2)
