"""Find exact format of tweet/post markets"""
import httpx

PROXY = "http://sp1keworkzxc:hZ5bwasMKy@176.106.62.204:50100"

print("Fetching open events...")
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
        for market in event.get("markets", []):
            all_markets.append(market.get("question", ""))
    offset += 500
    if len(events) < 500:
        break

print(f"Total: {len(all_markets)}")

# Find Andrew Tate markets
print("\n--- Andrew Tate ---")
for m in all_markets:
    if "andrew tate" in m.lower():
        print(f"  {m[:100]}")

# Verify "tweets" keyword safety
print("\n--- ALL 'tweets' matches (check for false positives) ---")
tweets_matches = [m for m in all_markets if "tweets" in m.lower()]
non_counting = [m for m in tweets_matches if "post" not in m.lower()]
if non_counting:
    print("  FALSE POSITIVES:")
    for m in non_counting[:10]:
        print(f"  !! {m[:100]}")
else:
    print(f"  All {len(tweets_matches)} matches are tweet counting markets. SAFE.")

# Verify "Truth Social" keyword safety
print("\n--- ALL 'Truth Social' matches ---")
ts_matches = [m for m in all_markets if "truth social" in m.lower()]
non_counting = [m for m in ts_matches if "post" not in m.lower()]
if non_counting:
    print("  EXTRA matches (not post counting):")
    for m in non_counting[:10]:
        print(f"  ?? {m[:100]}")
else:
    print(f"  All {len(ts_matches)} matches are Truth Social post counting. SAFE.")

# Test final keywords
print("\n" + "=" * 70)
print("PROPOSED KEYWORDS TEST:")
print("=" * 70)

new_keywords = ["tweets", "Truth Social", "Andrew Tate"]

for kw in new_keywords:
    matches = [m for m in all_markets if kw.lower() in m.lower()]
    print(f"\n'{kw}' -> {len(matches)} matches")
    seen = set()
    for m in matches[:8]:
        q = m[:90]
        if q not in seen:
            seen.add(q)
            print(f"  {q}")
    if len(matches) > 8:
        print(f"  ... +{len(matches) - 8} more")
