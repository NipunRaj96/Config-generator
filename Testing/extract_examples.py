import csv, json

rows = []
with open('OMS Activity.csv', encoding='utf-8-sig', errors='replace') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

locrgx_examples = []
for r in rows:
    cfg = r.get('config', '')
    if 'LOCRGX' in cfg and r.get('techStatus') == 'Done' and r.get('siteType') == 'ATS':
        try:
            c = json.loads(cfg)
            inner = list(c.values())[0]
            if isinstance(inner, dict) and 'LOCRGX' in inner:
                locrgx_examples.append({
                    'company':     r['companyName'],
                    'url':         r['careerSiteUrl'],
                    'config_url':  inner.get('URL', ''),
                    'locrgx':      inner.get('LOCRGX', ''),
                    'locrgxseq':   inner.get('LOCRGXSEQ', ''),
                    'move_to_jd':  inner.get('MOVE_TO_JD', 0),
                    'jdrgx1':      inner.get('JDRGX1', ''),
                    'jdrgxseq1':   inner.get('JDRGXSEQ1', ''),
                    'has_post':    '{{POST}}' in inner.get('URL', ''),
                    'has_header':  '{{HEADER}}' in inner.get('URL', ''),
                    'maxpages':    inner.get('MAXPAGESPARSE', ''),
                })
        except Exception:
            pass

print(f'Total LOCRGX examples: {len(locrgx_examples)}')
post_ex  = [e for e in locrgx_examples if e['has_post']]
get_ex   = [e for e in locrgx_examples if not e['has_post'] and e['config_url']]
page_ex  = [e for e in locrgx_examples if not e['config_url']]
print(f'POST: {len(post_ex)}, GET-with-custom-URL: {len(get_ex)}, Direct-page: {len(page_ex)}')

selected = post_ex[:2] + get_ex[:2] + page_ex[:2]
for i, ex in enumerate(selected):
    print()
    name = ex['company']
    print(f'--- [{i+1}] {name} ---')
    print(f'Career URL   : {ex["url"]}')
    print(f'Config URL   : {ex["config_url"][:100]}')
    print(f'LOCRGX       : {ex["locrgx"][:150]}')
    print(f'LOCRGXSEQ    : {ex["locrgxseq"]}')
    print(f'MOVE_TO_JD   : {ex["move_to_jd"]}')
    print(f'JDRGX1       : {ex["jdrgx1"][:100]}')
    print(f'JDRGXSEQ1    : {ex["jdrgxseq1"]}')
    print(f'POST?        : {ex["has_post"]}')

# Also check XPath SRP examples
srp_examples = []
for r in rows:
    cfg = r.get('config', '')
    if r.get('crawlerType') == 'SRPAUTOMATION' and 'xpath' in cfg:
        try:
            c = json.loads(cfg)
            inner = list(c.values())[0]
            if isinstance(inner, dict) and 'xpath' in inner:
                srp_examples.append({
                    'company': r['companyName'],
                    'url': r['careerSiteUrl'],
                    'xpath': inner.get('xpath', ''),
                    'navMethod': inner.get('navigationMethod', ''),
                    'isNext': inner.get('isNextFound', ''),
                    'loadMore': inner.get('loadMore', ''),
                })
        except Exception:
            pass

print(f'\nXPath-SRP examples: {len(srp_examples)}')
for ex in srp_examples[:4]:
    name = ex['company']
    print(f'  [{name}] xpath={ex["xpath"][:80]} nav={ex["navMethod"]} next={ex["isNext"]}')
