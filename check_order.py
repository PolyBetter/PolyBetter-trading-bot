"""Check if a specific order has builder attribution"""
import httpx

# Recent order from logs (placed AFTER builder was enabled)
order_id = "PASTE_ORDER_ID_HERE"  # e.g. from logs or CLOB

print(f"Checking order: {order_id[:20]}...")
print()

# 1. CLOB API
try:
    r = httpx.get(
        f"https://clob.polymarket.com/order/{order_id}",
        timeout=15, verify=False
    )
    print(f"CLOB /order status: {r.status_code}")
    if r.status_code == 200 and "<!DOCTYPE" not in r.text[:100]:
        data = r.json()
        print(f"Keys: {list(data.keys())}")
        for k, v in data.items():
            if "builder" in str(k).lower() or "attribution" in str(k).lower():
                print(f"  >>> BUILDER: {k} = {v}")
        print(f"  owner: {data.get('owner', 'N/A')}")
        print(f"  status: {data.get('status', 'N/A')}")
        print(f"  type: {data.get('type', 'N/A')}")
    else:
        print(f"  Response: {r.text[:200]}")
except Exception as e:
    print(f"CLOB Error: {e}")

print()

# 2. Try builders endpoint with wider search
try:
    r = httpx.get(
        "https://data-api.polymarket.com/v1/builders/leaderboard?timePeriod=DAY&limit=50&offset=0",
        timeout=15
    )
    data = r.json()
    print(f"Leaderboard DAY: {len(data)} builders")
    if data:
        # Show last few (smallest volume) to see minimum threshold
        for b in data[-3:]:
            print(f"  #{b.get('rank')} {b.get('builder','')[:25]:25} vol=${b.get('volume',0):>10,.2f} verified={b.get('verified')}")
except Exception as e:
    print(f"Error: {e}")

print()

# 3. Check ALL time with higher offset 
for offset in [0, 50, 100, 150, 200]:
    try:
        r = httpx.get(
            f"https://data-api.polymarket.com/v1/builders/leaderboard?timePeriod=ALL&limit=50&offset={offset}",
            timeout=15
        )
        data = r.json()
        found = [b for b in data if "019c564c" in str(b) or "nexora" in str(b).lower()]
        if found:
            print(f"FOUND at offset {offset}!")
            print(f"  {found[0]}")
            break
        if len(data) < 50:
            print(f"End of list at offset {offset} ({len(data)} entries). NOT FOUND.")
            break
    except:
        break
