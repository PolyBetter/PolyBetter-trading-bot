"""
Configuration Management
========================
Centralized configuration with validation and type safety.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime

# ==================== PATHS ====================
BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
PRESETS_FILE = BASE_DIR / "presets.json"
FAQ_FILE = BASE_DIR / "FAQ.md"

# ==================== API ENDPOINTS ====================
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
POLYGON_CHAIN_ID = 137


@dataclass
class Account:
    """Account configuration with runtime stats"""
    name: str
    enabled: bool = False
    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    proxy_wallet: str = ""
    proxy: str = ""
    
    # Runtime stats (not saved to config)
    start_time: datetime = field(default_factory=datetime.now, repr=False)
    orders_placed: int = field(default=0, repr=False)
    orders_filled: int = field(default=0, repr=False)
    total_volume: float = field(default=0.0, repr=False)
    errors_count: int = field(default=0, repr=False)
    
    def __post_init__(self):
        # Remove 0x prefix from private key if present
        if self.private_key.startswith("0x"):
            self.private_key = self.private_key[2:]
    
    def get_runtime(self) -> str:
        """Get formatted runtime"""
        delta = datetime.now() - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def get_proxy_url(self) -> Optional[str]:
        """Get normalized proxy URL"""
        if not self.proxy:
            return None
        return self.proxy if self.proxy.startswith("http") else f"http://{self.proxy}"
    
    def to_dict(self) -> dict:
        """Convert to dict for saving (excludes runtime fields)"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "private_key": self.private_key,
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "api_passphrase": self.api_passphrase,
            "proxy_wallet": self.proxy_wallet,
            "proxy": self.proxy
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Account':
        """Create from dict"""
        return cls(
            name=data.get("name", "Unknown"),
            enabled=data.get("enabled", False),
            private_key=data.get("private_key", ""),
            api_key=data.get("api_key", ""),
            api_secret=data.get("api_secret", ""),
            api_passphrase=data.get("api_passphrase", ""),
            proxy_wallet=data.get("proxy_wallet", ""),
            proxy=data.get("proxy", "")
        )


@dataclass
class TelegramConfig:
    """Telegram bot configuration"""
    bot_token: str = ""
    chat_id: str = ""
    allowed_user_id: int = 0  # 0 = allow all; set to your Telegram user ID to restrict access
    min_profit_multiplier: float = 5.0
    monitor_interval_seconds: int = 60
    auto_close_enabled: bool = True
    auto_close_pnl: float = 10.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TelegramConfig":
        uid = data.get("allowed_user_id", 0)
        if isinstance(uid, str) and uid.strip().isdigit():
            uid = int(uid.strip())
        elif not isinstance(uid, int):
            uid = 0
        return cls(
            bot_token=data.get("bot_token", ""),
            chat_id=data.get("chat_id", ""),
            allowed_user_id=uid,
            min_profit_multiplier=data.get("min_profit_multiplier", 5.0),
            monitor_interval_seconds=data.get("monitor_interval_seconds", 60),
            auto_close_enabled=data.get("auto_close_enabled", True),
            auto_close_pnl=data.get("auto_close_pnl", 10.0)
        )


@dataclass
class BuilderConfigOpt:
    """Optional Builder API credentials for order attribution (from config, no hardcoding)"""
    key: str = ""
    secret: str = ""
    passphrase: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "BuilderConfigOpt":
        if not data:
            return cls()
        return cls(
            key=data.get("key", ""),
            secret=data.get("secret", ""),
            passphrase=data.get("passphrase", ""),
        )


@dataclass 
class Settings:
    """Global settings"""
    # Network
    parallel_requests: int = 50
    request_timeout: int = 30
    
    # Sniper behavior
    no_candidates_pause_minutes: int = 5
    cache_reset_minutes: int = 30
    
    # Sell liquidity check
    check_sell_liquidity: bool = True
    min_bid_size: float = 5.0
    min_bid_count: int = 1
    
    # Order type
    sell_order_type: str = "limit"  # "limit" or "market"
    
    # Logging
    log_level: str = "DEBUG"
    log_max_size_mb: int = 50
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Settings':
        return cls(
            parallel_requests=data.get("parallel_requests", 50),
            request_timeout=data.get("request_timeout", 30),
            no_candidates_pause_minutes=data.get("no_candidates_pause_minutes", 5),
            cache_reset_minutes=data.get("cache_reset_minutes", 30),
            check_sell_liquidity=data.get("check_sell_liquidity", True),
            min_bid_size=data.get("min_bid_size", 5.0),
            min_bid_count=data.get("min_bid_count", 1),
            sell_order_type=data.get("sell_order_type", "limit"),
            log_level=data.get("log_level", "DEBUG"),
            log_max_size_mb=data.get("log_max_size_mb", 50)
        )


@dataclass
class Config:
    """Main configuration"""
    accounts: List[Account] = field(default_factory=list)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    settings: Settings = field(default_factory=Settings)
    builder: BuilderConfigOpt = field(default_factory=BuilderConfigOpt)

    def get_enabled_accounts(self) -> List[Account]:
        """Get only enabled accounts with API keys"""
        return [a for a in self.accounts if a.enabled and a.api_key]

    def to_dict(self) -> dict:
        d = {
            "accounts": [a.to_dict() for a in self.accounts],
            "telegram": self.telegram.to_dict(),
            "settings": self.settings.to_dict()
        }
        if self.builder.key or self.builder.secret or self.builder.passphrase:
            d["builder"] = {"key": self.builder.key, "secret": self.builder.secret, "passphrase": self.builder.passphrase}
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            accounts=[Account.from_dict(a) for a in data.get("accounts", [])],
            telegram=TelegramConfig.from_dict(data.get("telegram", {})),
            settings=Settings.from_dict(data.get("settings", {})),
            builder=BuilderConfigOpt.from_dict(data.get("builder", {})),
        )


def load_config() -> Config:
    """Load configuration from config.json"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return Config.from_dict(data)
        except Exception as e:
            print(f"[CONFIG] Error loading config: {e}")
    return Config()


def save_config(config: Config):
    """Save configuration to config.json"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)


def load_presets() -> dict:
    """Load presets from presets.json"""
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[CONFIG] Error loading presets: {e}")
    return {"presets": {}, "blocked_tags": {}, "blocked_keywords": {}}


def save_presets(presets: dict):
    """Save presets to presets.json"""
    with open(PRESETS_FILE, 'w', encoding='utf-8') as f:
        json.dump(presets, f, indent=2, ensure_ascii=False)


# ==================== PRESET DEFINITIONS ====================

DEFAULT_PRESETS = {
    "presets": {
        "aggressive": {
            "name": "🔥 Aggressive (Spam)",
            "description": "Max orders, low entry threshold",
            "order_amount": 0.10,
            "min_volume": 5000,
            "max_tick": 0.01,
            "min_ask": 0.001,
            "max_ask": 0.98,
            "min_hours_to_end": 12,
            "max_days_to_end": 60,
            "max_opposite_price": 0.98,
            "scan_interval": 0.3,
            "batch_size": 200,
            "block_sports": False,
            "block_crypto": False,
            "block_politics": False,
            "require_liquidity": False,
            "min_liquidity": 0
        },
        "medium": {
            "name": "⚖️ Medium (Balanced)",
            "description": "Balance between quantity and quality",
            "order_amount": 0.20,
            "min_volume": 10000,
            "max_tick": 0.01,
            "min_ask": 0.001,
            "max_ask": 0.95,
            "min_hours_to_end": 24,
            "max_days_to_end": 30,
            "max_opposite_price": 0.95,
            "scan_interval": 0.5,
            "batch_size": 100,
            "block_sports": True,
            "block_crypto": False,
            "block_politics": False,
            "require_liquidity": True,
            "min_liquidity": 100
        },
        "conservative": {
            "name": "🎯 Conservative (Quality)",
            "description": "Quality markets with high volume",
            "order_amount": 0.50,
            "min_volume": 50000,
            "max_tick": 0.01,
            "min_ask": 0.01,
            "max_ask": 0.90,
            "min_hours_to_end": 48,
            "max_days_to_end": 14,
            "max_opposite_price": 0.90,
            "scan_interval": 1.0,
            "batch_size": 50,
            "block_sports": True,
            "block_crypto": True,
            "block_politics": False,
            "require_liquidity": True,
            "min_liquidity": 500
        },
        "smart": {
            "name": "🧠 Smart (AI-filtered)",
            "description": "Smart filtering + liquidity analysis",
            "order_amount": 0.30,
            "min_volume": 25000,
            "max_tick": 0.01,
            "min_ask": 0.005,
            "max_ask": 0.92,
            "min_hours_to_end": 36,
            "max_days_to_end": 21,
            "max_opposite_price": 0.92,
            "scan_interval": 0.7,
            "batch_size": 80,
            "block_sports": True,
            "block_crypto": True,
            "block_politics": True,
            "require_liquidity": True,
            "min_liquidity": 300,
            # Smart-specific settings
            "require_sell_liquidity": True,
            "min_bid_depth": 3,
            "min_spread_pct": 0.02,
            "max_spread_pct": 0.50,
            "avoid_binary_sports": True,
            "avoid_low_activity": True
        }
    },
    "blocked_tags": {
        "sports": ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", 
                   "baseball", "hockey", "tennis", "golf", "mma", "ufc", "boxing",
                   "f1", "formula", "cricket", "rugby", "esports", "sports"],
        "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "token", "defi",
                   "altcoin", "memecoin", "solana", "cardano", "dogecoin"],
        "politics": ["election", "trump", "biden", "democrat", "republican", "congress",
                    "senate", "president", "governor", "political", "vote"]
    },
    "blocked_keywords": {
        "sports": ["win championship", "playoff", "super bowl", "world series", 
                   "stanley cup", "mvp award", "score", "touchdown", "goals"],
        "crypto": ["price above", "price below", "reach $", "hit $", "market cap"],
        "politics": ["win election", "electoral votes", "cabinet", "impeach"]
    }
}


def ensure_presets():
    """Ensure presets.json exists with defaults"""
    if not PRESETS_FILE.exists():
        save_presets(DEFAULT_PRESETS)
