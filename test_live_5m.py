"""Check if live 5-min options appear in gamma API"""
import httpx
import json
from datetime import datetime, timezone

PROXY = "http://sp1keworkzxc:hZ5bwasMKy@176.106.62.204:50100"

presets = json.load(open('presets.json', encoding='utf-8'))
p = presets['presets']['cryptoweather']
require_kws = p['require_keywords']
ban_kws = p['ban_keywords']

# Method 1: Direct slug lookup
print("=== Method 1: Direct event lookup ===")
try:
    r = httpx.get(
        "https://gamma-api.polymarket.com/events?slug=btc-updown-5m-1770881100",
        timeout=15, proxy=PROXY
    )
    data = r.json()
    if data:
        print(f"Found event: {data[0].get('title', '?')}")
        for m in data[0].get('markets', []):
            q = m.get('question', '')
            prices = m.get('outcomePrices', '[]')
            end = m.get('endDate', '')
            active = m.get('active', False)
            closed = m.get('closed', False)
            print(f"  Q: {q}")
            print(f"  Prices: {prices}")
            print(f"  End: {end} | Active: {active} | Closed: {closed}")
    else:
        print("  Not found by slug")
except Exception as e:
    print(f"  Error: {e}")

# Method 2: Search for recent 5-min markets
print("\n=== Method 2: Search for 5-min Up or Down (recent) ===")
try:
    # Try searching with different params
    r = httpx.get(
        "https://gamma-api.polymarket.com/events",
        params={
            "closed": "false",
            "limit": 50,
            "order": "startDate",
            "ascending": "false",
            "tag": "crypto",
        },
        timeout=15, proxy=PROXY
    )
    events = r.json()
    
    five_min_found = []
    for event in events:
        title = event.get('title', '')
        slug = event.get('slug', '')
        if 'up or down' in title.lower() and ('am' in title.lower() or 'pm' in title.lower() or '5m' in slug or '5 min' in title.lower()):
            five_min_found.append(event)
    
    print(f"Found {len(five_min_found)} potential 5-min events")
    for ev in five_min_found[:10]:
        title = ev.get('title', '')[:70]
        slug = ev.get('slug', '')[:50]
        print(f"  {title}")
        print(f"    slug: {slug}")
        for m in ev.get('markets', [])[:2]:
            q = m.get('question', '')[:70]
            prices = m.get('outcomePrices', '')
            print(f"    market: {q} | prices: {prices}")

except Exception as e:
    print(f"  Error: {e}")

# Method 3: Check CLOB for the specific market
print("\n=== Method 3: Search ALL recent events for 5-min patterns ===")
try:
    r = httpx.get(
        "https://gamma-api.polymarket.com/events",
        params={"closed": "false", "limit": 100, "order": "startDate", "ascending": "false"},
        timeout=15, proxy=PROXY
    )
    events = r.json()
    
    for event in events:
        for m in event.get('markets', []):
            q = m.get('question', '')
            ql = q.lower()
            if 'up or down' in ql and ('am-' in ql or 'pm-' in ql or '5 minute' in ql or '5 min' in ql):
                prices = m.get('outcomePrices', '')
                end = m.get('endDate', '')
                active = m.get('active', '')
                closed = m.get('closed', '')
                
                # Check our filter
                req = any(kw.lower() in ql for kw in require_kws)
                ban = any(kw.lower() in ql for kw in ban_kws)
                passed = "PASS" if (req and not ban) else "BLOCK"
                
                print(f"  [{passed}] {q[:75]}")
                print(f"         prices={prices[:40]} active={active} closed={closed}")
except Exception as e:
    print(f"  Error: {e}")

# Method 4: Try specific crypto endpoint
print("\n=== Method 4: Try gamma crypto-specific endpoints ===")
for endpoint in [
    "https://gamma-api.polymarket.com/events?tag=crypto&closed=false&limit=20&order=startDate&ascending=false",
    "https://gamma-api.polymarket.com/events?slug_contains=5m&closed=false&limit=20",
    "https://gamma-api.polymarket.com/events?title_contains=5+minute&closed=false&limit=20",
]:
    try:
        r = httpx.get(endpoint, timeout=10, proxy=PROXY)
        events = r.json()
        found = 0
        for ev in events:
            for m in ev.get('markets', []):
                q = m.get('question', '').lower()
                if 'up or down' in q and ('am-' in q or 'pm-' in q or '5 min' in q):
                    found += 1
                    if found <= 3:
                        print(f"  {m.get('question', '')[:70]}")
        if found:
            print(f"  -> Found {found} 5-min markets from: {endpoint[:60]}...")
    except:
        pass
