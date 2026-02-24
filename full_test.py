"""Full end-to-end test of cryptoweather preset on LIVE data"""
import json
import httpx
from datetime import datetime, timezone, timedelta

PROXY = "http://sp1keworkzxc:hZ5bwasMKy@176.106.62.204:50100"

# Load preset
presets = json.load(open('presets.json', encoding='utf-8'))
p = presets['presets']['cryptoweather']

require_kws = p['require_keywords']
ban_kws = p['ban_keywords']
max_tick = p['max_tick']
max_ask = p['max_ask']
min_ask = p['min_ask']
order_amount = p['order_amount']
min_hours = p['min_hours_to_end']
max_days = p['max_days_to_end']

print(f"Preset: {p['name']}")
print(f"max_tick={max_tick}, max_ask={max_ask}, order_amount={order_amount}")
print(f"min_hours_to_end={min_hours} ({min_hours*60:.0f}min), max_days={max_days}")
print()

# Fetch all open events
print("Fetching ALL open events...")
all_markets = []
offset = 0
while True:
    r = httpx.get(
        f"https://gamma-api.polymarket.com/events?closed=false&limit=500&offset={offset}",
        timeout=30, proxy=PROXY
    )
    events = r.json()
    if not events:
        break
    for event in events:
        tags = [t.get('label', '').lower() if isinstance(t, dict) else str(t).lower() 
                for t in event.get('tags', [])]
        for market in event.get('markets', []):
            all_markets.append({
                'question': market.get('question', ''),
                'outcomePrices': market.get('outcomePrices', '[]'),
                'endDate': market.get('endDate', ''),
                'volume': float(market.get('volume', 0) or 0),
                'active': market.get('active', True),
                'closed': market.get('closed', False),
                'tags': tags,
                'conditionId': market.get('conditionId', ''),
            })
    offset += 500
    if len(events) < 500:
        break

print(f"Total markets loaded: {len(all_markets)}")

# Filter step by step
now = datetime.now(timezone.utc)

# Step 1: keyword filter
step1 = []
for m in all_markets:
    q = m['question'].lower()
    req_match = any(kw.lower() in q for kw in require_kws)
    ban_match = any(kw.lower() in q for kw in ban_kws)
    if req_match and not ban_match and not m['closed']:
        step1.append(m)

print(f"\nAfter keyword filter: {len(step1)} markets")

# Step 2: time filter
step2 = []
for m in step1:
    end_str = m.get('endDate', '')
    if not end_str:
        step2.append(m)
        continue
    try:
        end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        hours_left = (end - now).total_seconds() / 3600
        if hours_left >= min_hours and hours_left <= max_days * 24:
            m['hours_left'] = hours_left
            step2.append(m)
    except:
        step2.append(m)

print(f"After time filter: {len(step2)} markets")

# Step 3: price filter (check outcomePrices)
step3 = []
for m in step2:
    prices = m.get('outcomePrices', '[]')
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except:
            prices = []
    prices = [float(x) for x in prices] if prices else []
    
    has_valid_price = False
    for price in prices:
        if min_ask <= price <= max_ask:
            has_valid_price = True
            break
    
    if has_valid_price:
        m['prices'] = prices
        step3.append(m)

print(f"After price filter (${min_ask}-${max_ask}): {len(step3)} markets")

# Categorize results
categories = {
    'weather': [],
    '5min_options': [],
    '1h_4h_options': [],
    'tweets': [],
    'unknown': [],
}

for m in step3:
    q = m['question'].lower()
    hours = m.get('hours_left', 999)
    
    if any(kw.lower() in q for kw in ['temperature', 'snow', 'rain', 'weather', '°f', '°c', 'celsius', 'fahrenheit', 'forecast']):
        categories['weather'].append(m)
    elif 'up or down' in q:
        # Check if 5-minute (very short end time or has PM-PM pattern)
        if hours < 0.5 or ('pm-' in q.lower() or 'am-' in q.lower() or 'pm et' in q.lower()):
            categories['5min_options'].append(m)
        else:
            categories['1h_4h_options'].append(m)
    elif any(kw.lower() in q for kw in ['tweets', 'truth social', 'andrew tate post']):
        categories['tweets'].append(m)
    else:
        categories['unknown'].append(m)

print(f"\n{'='*70}")
print("CATEGORIZED RESULTS:")
print(f"{'='*70}")

for cat, markets in categories.items():
    print(f"\n--- {cat.upper()} ({len(markets)} markets) ---")
    for m in markets[:5]:
        q = m['question'][:70]
        prices = m.get('prices', [])
        hours = m.get('hours_left', 0)
        
        # Determine order params
        # Tick would be the lowest price
        min_price = min(prices) if prices else 0
        if min_price > 0 and min_price <= max_tick:
            shares = int(order_amount / min_price)
            cost = shares * min_price
        else:
            shares = 0
            cost = 0
        
        time_str = f"{hours:.1f}h" if hours < 24 else f"{hours/24:.1f}d"
        print(f"  {q}")
        print(f"    prices={[f'{x:.3f}' for x in prices[:3]]} | ends={time_str} | {shares} shares @ ${min_price:.3f} = ${cost:.2f}")
    
    if len(markets) > 5:
        print(f"  ... +{len(markets) - 5} more")

# Check for UNKNOWN (potentially unwanted) markets
if categories['unknown']:
    print(f"\n{'!'*70}")
    print(f"WARNING: {len(categories['unknown'])} UNKNOWN/UNCLASSIFIED markets!")
    print(f"{'!'*70}")
    for m in categories['unknown']:
        print(f"  ?? {m['question'][:80]}")

# Summary
print(f"\n{'='*70}")
print("SUMMARY:")
print(f"{'='*70}")
total = sum(len(v) for v in categories.values())
print(f"Total matching markets: {total}")
print(f"  Weather:        {len(categories['weather'])}")
print(f"  5-min options:  {len(categories['5min_options'])}")
print(f"  1h/4h options:  {len(categories['1h_4h_options'])}")
print(f"  Tweets/posts:   {len(categories['tweets'])}")
print(f"  UNKNOWN:        {len(categories['unknown'])} {'<-- CHECK THESE!' if categories['unknown'] else '(clean)'}")

# Verify order sizes
print(f"\nORDER SIZES:")
print(f"  At tick $0.001: {int(order_amount/0.001)} shares x $0.001 = ${int(order_amount/0.001)*0.001:.2f}")
print(f"  At tick $0.005: {int(order_amount/0.005)} shares x $0.005 = ${int(order_amount/0.005)*0.005:.2f}")
print(f"  At tick $0.010: {int(order_amount/0.010)} shares x $0.010 = ${int(order_amount/0.010)*0.010:.2f}")
