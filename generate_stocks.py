"""Generate compact stocks.json for search feature."""
import json, os, sys

src = 'apps/dsa-web/public/stocks.index.json'
out = sys.argv[1] if len(sys.argv) > 1 else 'reports/stocks.json'

if os.path.exists(src):
    with open(src, encoding='utf-8') as f:
        data = json.load(f)
    compact = []
    seen = set()
    for item in data:
        if len(item) >= 5 and item[1] not in seen:
            seen.add(item[1])
            compact.append({'c': item[1], 'n': item[2], 'p': item[4]})
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(compact, f, ensure_ascii=False, separators=(',', ':'))
    print(f'stocks.json: {len(compact)} entries')
else:
    print(f'Source not found: {src}')
