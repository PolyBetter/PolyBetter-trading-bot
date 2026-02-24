# PolyBetter — Polymarket Trading Tool v3.0

Trading automation and sniper for [Polymarket](https://polymarket.com): limit orders, smart filters, multi-account support.

## What's in v3.0

### Performance
- **Async httpx** — parallel requests for fast market scanning
- **Connection pooling** — connection reuse
- **Optimized cache** — fewer duplicate API calls

### Logging
- **Full logs to file** — nothing truncated
- **JSON logs** — for parsing and analysis
- **Dedicated error log** — quick troubleshooting
- **Clean console output** — only what matters

### CSV tracking
- **trades_*.csv** — all orders with full details
- **positions_*.csv** — position snapshots
- **pnl_*.csv** — P&L tracking

### Strategies
- **Limit Sniper** — high volume of small limit orders at min tick
- **Smart Sniper** — scoring, liquidity and spread filters

### Telegram bot
- **aiogram 3.x** — async bot
- **1-minute monitoring** (configurable)
- **Back button** on every screen
- **Tools** — analysis, stats, positions

## Project structure

```
PolyBetter/
├── main.py                 # Entry point
├── config_template.json    # Config template (copy to config.json)
├── presets.json            # Strategy presets
│
├── core/
│   ├── config.py           # Config loading
│   ├── logger.py           # Logging
│   ├── client.py           # CLOB client
│   └── data_api.py         # Data API client
│
├── strategies/
│   ├── base.py             # Base strategy
│   ├── sniper.py           # Limit Sniper
│   └── smart_sniper.py     # Smart Sniper
│
├── trackers/
│   └── csv_tracker.py      # CSV tracking
│
├── bot/
│   └── telegram_bot_v2.py # Telegram bot (aiogram)
│
├── tools/
│   ├── analyzer.py         # Market analysis
│   └── simulator.py        # Strategy simulation
│
├── logs/                   # Created at runtime
│   ├── polymarket.log
│   ├── polymarket.json.log
│   └── polymarket_errors.log
│
└── data/                   # Created at runtime
    ├── trades_*.csv
    ├── positions_*.csv
    └── pnl_*.csv
```

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Interactive menu
```bash
python main.py
```

### Direct run
```bash
python main.py sniper      # Limit Sniper
python main.py smart       # Smart Sniper
python main.py bot         # Telegram bot
python main.py analyze     # Market analyzer
python main.py simulate    # Strategy simulator
```

## Configuration

1. Copy `config_template.json` to `config.json`.
2. Fill in accounts (private key, API keys from [Polymarket CLOB](https://clob.polymarket.com)), proxy if needed, optional Telegram.

**Important:** Never commit `config.json` (it is in `.gitignore`). Use `config_template.json` as reference.

### config.json overview
```json
{
  "accounts": [{
    "name": "Account 1",
    "enabled": true,
    "private_key": "...",
    "api_key": "...",
    "api_secret": "...",
    "api_passphrase": "...",
    "proxy_wallet": "0x...",
    "proxy": "http://user:pass@host:port"
  }],
  "telegram": {
    "bot_token": "...",
    "chat_id": "...",
    "allowed_user_id": 0,
    "min_profit_multiplier": 5,
    "monitor_interval_seconds": 60,
    "auto_close_enabled": true,
    "auto_close_pnl": 10
  },
  "settings": {
    "check_sell_liquidity": true,
    "min_bid_size": 5,
    "sell_order_type": "limit"
  }
}
```

## Presets

| Preset        | Description                    | Order size | Min volume |
|---------------|--------------------------------|------------|------------|
| aggressive    | Max orders, low thresholds     | $0.10      | $5k        |
| medium        | Balance of quantity & quality  | $0.20      | $10k       |
| conservative  | Quality only, high volume      | $0.50      | $50k       |
| smart         | Scoring, liquidity, spread     | $0.30      | —          |

Presets are defined in `presets.json`; you can add or edit them.

## Logs

- `logs/polymarket.log` — full text log
- `logs/polymarket.json.log` — JSON log
- `logs/polymarket_errors.log` — errors only

## CSV tracking

- **trades_YYYY-MM-DD.csv** — every order (timestamp, account, token, side, price, size, status, error)
- **positions_*.csv** — position snapshots
- **pnl_*.csv** — P&L over time

## Telegram bot

Commands: `/start`, `/balance`, `/positions`, `/profit`, `/orders`.  
Optional: restrict access with `allowed_user_id` in config.

## Security

- Keep `config.json` local and out of version control.
- Use proxies if you want to hide your IP.
- API keys are from Polymarket; never share them.

## License

MIT License
