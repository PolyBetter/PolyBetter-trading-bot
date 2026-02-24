import json

data = json.load(open('presets.json', encoding='utf-8'))

# Check crypto_options
o = data['presets']['crypto_options']
print("=== crypto_options ===")
print(f"  fixed_order_price: ${o['fixed_order_price']}")
print(f"  fixed_order_size:  {o['fixed_order_size']} shares")
print(f"  auto_take_profit:  {o.get('auto_take_profit', True)}")
print(f"  Cost per bet:      {o['fixed_order_size']} x ${o['fixed_order_price']} = ${o['fixed_order_size'] * o['fixed_order_price']:.2f}")
print(f"  If wins:           {o['fixed_order_size']} x $1.00 = ${o['fixed_order_size']:.2f}")

# Check weather_tweets
w = data['presets']['weather_tweets']
print()
print("=== weather_tweets ===")
print(f"  fixed_order_price: ${w['fixed_order_price']}")
print(f"  fixed_order_size:  {w['fixed_order_size']} shares")
print(f"  auto_take_profit:  {w.get('auto_take_profit', True)}")
tp_ratio = w.get('take_profit_ratio', 0)
tp_price = w.get('take_profit_price', 0)
tp_shares = int(w['fixed_order_size'] * tp_ratio)
hold_shares = w['fixed_order_size'] - tp_shares
print(f"  TP:                sell {tp_shares} shares @ ${tp_price}")
print(f"  Hold:              {hold_shares} shares until resolution")
print(f"  Cost per bet:      {w['fixed_order_size']} x ${w['fixed_order_price']} = ${w['fixed_order_size'] * w['fixed_order_price']:.3f}")

# Filter test
print()
print("=== FILTER TEST ===")

require_opt = o['require_keywords']
ban_opt = o['ban_keywords']
require_wt = w['require_keywords']
ban_wt = w['ban_keywords']

tests = [
    ("Bitcoin Up or Down - Feb 13, 2:10AM-2:15AM ET", "options", "crypto_options"),
    ("Ethereum Up or Down - Feb 13, 12:00AM-12:15AM ET", "options", "crypto_options"),
    ("Solana Up or Down on February 12?", "options", "crypto_options"),
    ("Bitcoin Up or Down on February 14, 2AM ET", "options", "crypto_options"),
    ("Nikkei 225 (NIK) Up or Down on February 12?", "BLOCK", "crypto_options"),
    ("Will the price of Bitcoin be above $62,000?", "BLOCK", "crypto_options"),
    ("Highest temperature in NYC on Feb 13?", "weather", "weather_tweets"),
    ("Will it snow in Chicago on February 13?", "weather", "weather_tweets"),
    ("Will Elon Musk post 320-339 tweets Feb 6-13?", "tweets", "weather_tweets"),
    ("Will Trump post 20-39 Truth Social posts?", "tweets", "weather_tweets"),
    ("Will Andrew Tate post 220-249 posts Feb 13-20?", "tweets", "weather_tweets"),
    ("Snowboard Halfpipe Women gold medal?", "BLOCK", "weather_tweets"),
]

for market, expected, preset_name in tests:
    if preset_name == "crypto_options":
        req = require_opt
        ban = ban_opt
    else:
        req = require_wt
        ban = ban_wt
    
    req_match = any(kw.lower() in market.lower() for kw in req)
    ban_match = any(kw.lower() in market.lower() for kw in ban)
    passed = req_match and not ban_match
    
    if expected == "BLOCK":
        ok = not passed
    else:
        ok = passed
    
    icon = "v" if ok else "X WRONG"
    status = "PASS" if passed else "BLOCK"
    print(f"  [{icon}] {status:5} [{preset_name:14}] {market[:55]}")

print()
print(f"Total presets: {len(data['presets'])}")
print("JSON: VALID")
