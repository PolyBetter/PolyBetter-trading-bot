"""Search for 5-minute crypto options specifically"""
import httpx
import json

PROXY = "http://sp1keworkzxc:hZ5bwasMKy@176.106.62.204:50100"

# Load preset
presets = json.load(open('presets.json', encoding='utf-8'))
p = presets['presets']['cryptoweather']
require_kws = p['require_keywords']
ban_kws = p['ban_keywords']

# Search for ALL markets with "Up or Down" including closed/resolved
print("=== Searching for 5-min options (including recent/closed) ===\n")

# Method 1: Search gamma API for recent Up or Down markets
for query in ["5 minute", "5M", "Up or Down"]:
    print(f"--- Search: '{query}' ---")
    try:
        r = httpx.get(
            f"https://gamma-api.polymarket.com/events?closed=false&limit=20&tag=crypto",
            params={"limit": 20, "closed": False},
            timeout=15, proxy=PROXY
        )
    except:
        pass

# Search for markets with time patterns like "AM-" or "PM-"
print("\n--- Searching for time-range options (AM-/PM- pattern) ---")
all_markets = []
offset = 0
while offset < 2000:
    r = httpx.get(
        f"https://gamma-api.polymarket.com/events?closed=false&limit=500&offset={offset}",
        timeout=30, proxy=PROXY
    )
    events = r.json()
    if not events:
        break
    for event in events:
        for market in event.get('markets', []):
            q = market.get('question', '')
            if 'up or down' in q.lower():
                all_markets.append({
                    'question': q,
                    'endDate': market.get('endDate', ''),
                    'active': market.get('active', True),
                    'closed': market.get('closed', False),
                    'outcomePrices': market.get('outcomePrices', '[]'),
                })
    offset += 500
    if len(events) < 500:
        break

print(f"Found {len(all_markets)} 'Up or Down' markets total\n")

# Categorize by type
five_min = []
fifteen_min = []
hourly = []
four_hour = []
daily = []
other = []

for m in all_markets:
    q = m['question']
    ql = q.lower()
    
    # Check for time range patterns like "1:45AM-1:50AM" (5-min)
    has_time_range = ('am-' in ql or 'pm-' in ql) and ('am et' in ql or 'pm et' in ql)
    
    if '5 minute' in ql or '5 min' in ql:
        five_min.append(m)
    elif has_time_range:
        # Parse time range to determine duration
        # 5-min: "1:45AM-1:50AM" or "9:45PM-9:50PM"
        # 15-min: "1:45AM-2:00AM"
        five_min.append(m)  # time ranges are typically 5 or 15 min
    elif '15 min' in ql:
        fifteen_min.append(m)
    elif any(x in ql for x in ['hourly', '1 hour', '(1h)']):
        hourly.append(m)
    elif any(x in ql for x in ['4 hour', '(4h)']):
        four_hour.append(m)
    else:
        # Check end date to guess type
        other.append(m)

print(f"5-min options:   {len(five_min)}")
print(f"15-min options:  {len(fifteen_min)}")
print(f"Hourly:          {len(hourly)}")
print(f"4-hour:          {len(four_hour)}")
print(f"Other:           {len(other)}")

if five_min:
    print(f"\n--- 5-MIN EXAMPLES ---")
    for m in five_min[:10]:
        prices = m.get('outcomePrices', '[]')
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: prices = []
        prices = [float(x) for x in prices] if prices else []
        
        # Check keyword filter
        q = m['question']
        req = any(kw.lower() in q.lower() for kw in require_kws)
        ban = any(kw.lower() in q.lower() for kw in ban_kws)
        passed = req and not ban
        
        print(f"  {'PASS' if passed else 'BLOCK':5} | {q[:75]}")
        if prices:
            print(f"         prices={[f'{x:.3f}' for x in prices[:3]]} closed={m['closed']}")

# Show some 'other' to understand what they look like
if other:
    print(f"\n--- OTHER 'Up or Down' EXAMPLES (first 15) ---")
    for m in other[:15]:
        q = m['question']
        req = any(kw.lower() in q.lower() for kw in require_kws)
        ban = any(kw.lower() in q.lower() for kw in ban_kws)
        passed = req and not ban
        print(f"  {'PASS' if passed else 'BLOCK':5} | {q[:80]}")
