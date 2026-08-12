import csv, json, collections, re
from urllib.parse import urlparse

rows = []
with open('OMS Activity.csv', encoding='utf-8-sig', errors='replace') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

done = [r for r in rows if r['techStatus'] == 'Done']

other_done = []
for r in done:
    cfg = r.get('config', '')
    if 'LOCRGX' not in cfg and 'LOCJSON' not in cfg and 'PARENT_RULE_NAME' not in cfg:
        try:
            c = json.loads(cfg)
            inner = list(c.values())[0]
            if isinstance(inner, dict):
                other_done.append((r, inner))
            else:
                other_done.append((r, {}))
        except Exception:
            other_done.append((r, {}))

print(f'Other Done configs (parseable): {len(other_done)}')
srp_auto = sum(1 for r, _ in other_done if r.get('crawlerType') == 'SRPAUTOMATION')
jperl    = sum(1 for r, _ in other_done if r.get('crawlerType') == 'JPERL')
print(f'crawlerType: SRPAUTOMATION={srp_auto}, JPERL={jperl}')

print('\n=== Sample SRPAUTOMATION other configs ===')
n = 0
for r, inner in other_done:
    if r.get('crawlerType') == 'SRPAUTOMATION' and n < 5:
        name = r['companyName']
        url  = r['careerSiteUrl']
        keys = list(inner.keys())[:12]
        print(f'  [{n+1}] {name}  |  {url}')
        print(f'       Keys: {keys}')
        for k in ['URL', 'XPATH', 'JOBLINK', 'JOBID', 'SUBPARSE']:
            if k in inner:
                print(f'       {k}: {str(inner[k])[:100]}')
        print()
        n += 1

print('\n=== Sample JPERL other configs ===')
n = 0
for r, inner in other_done:
    if r.get('crawlerType') == 'JPERL' and n < 5:
        name = r['companyName']
        url  = r['careerSiteUrl']
        keys = list(inner.keys())[:12]
        print(f'  [{n+1}] {name}  |  {url}')
        print(f'       Keys: {keys}')
        for k in ['URL', 'SUBPARSE', 'XPATH', 'LOCJSON', 'LOCJSONSEQ']:
            if k in inner:
                print(f'       {k}: {str(inner[k])[:100]}')
        print()
        n += 1

# ---- What makes an SRP DONE config? ----
print('\n=== All DONE-SRP config structures ===')
done_srp = [r for r in rows if r['techStatus'] == 'Done' and r['siteType'] == 'SRP']
srp_key_dist = collections.Counter()
for r in done_srp:
    cfg_str = r.get('config', '')
    try:
        c = json.loads(cfg_str)
        inner = list(c.values())[0]
        if isinstance(inner, dict):
            for k in inner:
                srp_key_dist[k] += 1
    except Exception:
        pass
print('Keys present in Done-SRP configs:')
for k, v in srp_key_dist.most_common(20):
    print(f'  {k:30s}: {v:3d}  ({v/len(done_srp)*100:.0f}%)')

# ---- Parent rules that appear in real OMS data but NOT in our KB ----
print('\n=== Parent rules missing from our KB ===')
import os
with open('knowledge_base/ats_platforms.json') as f:
    kb = json.load(f)
kb_rules = {p['parent_rule_name'] for p in kb}

oms_rules = collections.Counter()
for r in rows:
    cfg = r.get('config', '')
    if 'PARENT_RULE_NAME' in cfg:
        try:
            c = json.loads(cfg)
            inner = list(c.values())[0]
            if isinstance(inner, dict) and 'PARENT_RULE_NAME' in inner:
                oms_rules[inner['PARENT_RULE_NAME']] += 1
        except Exception:
            pass

print(f'Rules in OMS data: {len(oms_rules)}')
print(f'Rules in our KB:   {len(kb_rules)}')
missing = {r: c for r, c in oms_rules.items() if r not in kb_rules}
print(f'Missing from KB:   {len(missing)}')
for rule, cnt in sorted(missing.items(), key=lambda x: -x[1]):
    print(f'  {rule:40s}: {cnt}')
