"""
Base Strategy Classes
====================
Foundation for all trading strategies.
"""

import json
import re
import time
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple, Any

from core.config import Account, load_presets, GAMMA_API, CLOB_API
from core.logger import get_logger
from core.client import get_clob_client, patch_httpx_for_proxy
from core.data_api import DataAPI
from trackers.csv_tracker import get_trade_tracker

logger = get_logger()


@dataclass
class MarketCandidate:
    """A market that passed filters and is ready for order placement"""
    token_id: str
    market_id: str
    question: str
    outcome: str
    price: float
    tick_size: float
    volume: float
    liquidity: float
    tags: List[str]
    end_date: Optional[datetime] = None
    
    # Analysis data
    has_sell_liquidity: bool = True
    bid_depth: float = 0
    ask_depth: float = 0
    spread: float = 0


class MarketFilter:
    """
    Reusable market filter based on preset configuration.
    
    Filters markets based on:
    - Volume
    - Liquidity
    - End date
    - Price range
    - Blocked tags/keywords
    """
    
    def __init__(self, preset_name: str = "medium"):
        presets_data = load_presets()
        self.preset = presets_data.get("presets", {}).get(preset_name, {})
        self.blocked_tags = presets_data.get("blocked_tags", {})
        self.blocked_keywords = presets_data.get("blocked_keywords", {})
        self.preset_name = preset_name
    
    def is_tag_blocked(self, tags: List[str], block_type: str) -> bool:
        """Check if any tag matches blocked list"""
        blocked_list = self.blocked_tags.get(block_type, [])
        for tag in tags:
            tag_lower = tag.lower() if isinstance(tag, str) else ""
            for blocked in blocked_list:
                if blocked in tag_lower:
                    return True
        return False
    
    def is_keyword_blocked(self, text: str, block_type: str) -> bool:
        """Check if text contains blocked keywords"""
        blocked_list = self.blocked_keywords.get(block_type, [])
        text_lower = text.lower()
        for keyword in blocked_list:
            if keyword in text_lower:
                return True
        return False
    
    def filter_market(self, market: Dict) -> Tuple[bool, str]:
        """
        Check if market passes all filters.
        
        Returns:
            Tuple[bool, str]: (passes_filter, reason_for_rejection)
        """
        # Extract tags (normalize different formats)
        tags = []
        for t in market.get('tags', []) + market.get('event_tags', []):
            if isinstance(t, dict):
                tag = t.get('label', '') or t.get('slug', '') or ''
            else:
                tag = str(t)
            if tag:
                tags.append(tag.lower())
        
        # Full text for keyword matching
        text = f"{market.get('question', '')} {market.get('event_title', '')}".lower()
        
        # ===== REQUIRE TAGS (preset-specific) =====
        # If require_any_tag is set, market MUST have at least one of these tags
        require_any_tag = self.preset.get('require_any_tag', [])
        if require_any_tag:
            required_lower = [t.lower() for t in require_any_tag]
            found_tag = False
            for tag in tags:
                if tag in required_lower:
                    found_tag = True
                    break
            if not found_tag:
                return False, 'missing_required_tag'
        
        # ===== REQUIRE KEYWORDS (preset-specific) =====
        # If require_keywords is set, market MUST contain at least one of these
        require_keywords = self.preset.get('require_keywords', [])
        if require_keywords:
            found = False
            for keyword in require_keywords:
                kw_lower = keyword.lower()
                # For keywords with non-ASCII chars (°F, °C etc), use substring match
                # For ASCII-only keywords, use word boundary to avoid false positives
                if kw_lower.isascii():
                    pattern = r'\b' + re.escape(kw_lower) + r'\b'
                    if re.search(pattern, text):
                        found = True
                        break
                else:
                    # Substring match for mixed/special characters
                    if kw_lower in text:
                        found = True
                        break
            if not found:
                return False, 'missing_required_keyword'
        
        # ===== BAN KEYWORDS (preset-specific) =====
        # If ban_keywords is set, market MUST NOT contain any of these
        ban_keywords = self.preset.get('ban_keywords', [])
        if ban_keywords:
            for keyword in ban_keywords:
                if keyword.lower() in text:
                    return False, f'banned_keyword:{keyword}'
        
        # ===== TAG BLOCKS =====
        if self.preset.get('block_sports', False):
            if self.is_tag_blocked(tags, 'sports'):
                return False, 'blocked:sports_tag'
        
        if self.preset.get('block_crypto', False):
            if self.is_tag_blocked(tags, 'crypto'):
                return False, 'blocked:crypto_tag'
        
        if self.preset.get('block_politics', False):
            if self.is_tag_blocked(tags, 'politics'):
                return False, 'blocked:politics_tag'
        
        # ===== KEYWORD BLOCKS (global) =====
        if self.preset.get('block_sports', False):
            if self.is_keyword_blocked(text, 'sports'):
                return False, 'blocked:sports_keyword'
        
        if self.preset.get('block_crypto', False):
            if self.is_keyword_blocked(text, 'crypto'):
                return False, 'blocked:crypto_keyword'
        
        if self.preset.get('block_politics', False):
            if self.is_keyword_blocked(text, 'politics'):
                return False, 'blocked:politics_keyword'
        
        # ===== VOLUME =====
        volume = float(market.get('volume', 0) or market.get('volumeNum', 0) or 0)
        min_volume = self.preset.get('min_volume', 10000)
        if volume < min_volume:
            return False, f'volume:{volume}<{min_volume}'
        
        # ===== LIQUIDITY =====
        if self.preset.get('require_liquidity', False):
            liquidity = float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0)
            min_liquidity = self.preset.get('min_liquidity', 0)
            if liquidity < min_liquidity:
                return False, f'liquidity:{liquidity}<{min_liquidity}'
        
        # ===== TIME RANGE DURATION FILTER =====
        # If require_time_range_minutes is set, only allow markets with exactly that duration
        # Parses patterns like "11:55AM-12:00PM ET" from the question text
        required_minutes = self.preset.get('require_time_range_minutes', 0)
        if required_minutes > 0:
            question = market.get('question', '')
            time_range_match = re.search(
                r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*ET',
                question, re.IGNORECASE
            )
            if not time_range_match:
                # No time range found — not a timed option (hourly/daily/etc), reject
                return False, 'no_time_range'
            
            # Parse start and end times
            sh, sm, sap = int(time_range_match.group(1)), int(time_range_match.group(2)), time_range_match.group(3).upper()
            eh, em, eap = int(time_range_match.group(4)), int(time_range_match.group(5)), time_range_match.group(6).upper()
            
            # Convert to 24h minutes-since-midnight
            def to_minutes(h, m, ap):
                if ap == 'AM':
                    if h == 12:
                        h = 0
                elif ap == 'PM':
                    if h != 12:
                        h += 12
                return h * 60 + m
            
            start_min = to_minutes(sh, sm, sap)
            end_min = to_minutes(eh, em, eap)
            
            # Handle midnight crossing (e.g., 11:55PM-12:00AM)
            duration = end_min - start_min
            if duration <= 0:
                duration += 24 * 60
            
            if duration != required_minutes:
                return False, f'time_range:{duration}min!={required_minutes}min'
        
        # ===== END DATE =====
        end_date = market.get('endDate') or market.get('end_date_iso')
        if end_date:
            try:
                if isinstance(end_date, str):
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                else:
                    end_dt = datetime.fromtimestamp(end_date, tz=timezone.utc)
                
                hours_until = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                
                min_hours = self.preset.get('min_hours_to_end', 24)
                if hours_until < min_hours:
                    return False, f'ending_soon:{hours_until:.0f}h<{min_hours}h'
                
                max_days = self.preset.get('max_days_to_end', 30)
                if hours_until > max_days * 24:
                    return False, f'ending_late:{hours_until/24:.0f}d>{max_days}d'
                    
            except Exception as e:
                pass  # Skip date check on parse error
        
        return True, 'ok'
    
    def filter_price(self, price: float) -> Tuple[bool, str]:
        """Check if price is within acceptable range"""
        min_ask = self.preset.get('min_ask', 0.001)
        max_ask = self.preset.get('max_ask', 0.95)
        
        if price < min_ask:
            return False, f'price_low:{price}<{min_ask}'
        if price > max_ask:
            return False, f'price_high:{price}>{max_ask}'
        
        return True, 'ok'
    
    def is_skewed_market(self, prices: List[float]) -> bool:
        """
        Check if market is heavily skewed (one outcome dominates).
        Skewed markets are usually less interesting.
        """
        max_opposite = self.preset.get('max_opposite_price', 0.95)
        
        if len(prices) >= 2:
            max_price = max(prices)
            if max_price > max_opposite:
                return True
        
        return False


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.
    
    Subclasses must implement:
    - scan(): Find and place orders
    - run(): Main loop
    """
    
    def __init__(self, account: Account, preset_name: str = "medium"):
        self.account = account
        self.preset_name = preset_name
        self.filter = MarketFilter(preset_name)
        self.preset = self.filter.preset
        
        # State tracking
        self.excluded_tokens: Set[str] = set()  # Never touch again
        self.placed_tokens: Set[str] = set()    # Placed this session
        self.tick_cache: Dict[str, float] = {}
        
        # Take-profit tracking: token_id -> True if SELL order already placed
        self.take_profit_placed: Set[str] = set()
        
        # Runtime stats
        self.running = True
        self.cycle = 0
        self.orders_placed = 0
        self.orders_failed = 0
        self.sell_orders_placed = 0  # Take-profit sell orders
        self.start_time = datetime.now()
        self.last_reset = time.time()
        
        # API clients (initialized in init())
        self.data_api: Optional[DataAPI] = None
        self.clob_client = None
        
        # Get settings
        from core.config import load_config
        config = load_config()
        self.settings = config.settings
        
        # Trade tracker
        self.trade_tracker = get_trade_tracker()
    
    def get_runtime(self) -> str:
        """Get formatted runtime"""
        delta = datetime.now() - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def init(self) -> bool:
        """
        Initialize strategy.
        Sets up API clients, loads existing orders/positions.
        
        Returns:
            bool: True if initialization successful
        """
        logger.info(
            f"Initializing {self.__class__.__name__}",
            account=self.account.name,
            action="STRATEGY_INIT",
            details={"preset": self.preset_name}
        )
        
        # Setup proxy
        if self.account.proxy:
            patch_httpx_for_proxy(self.account.proxy, force=True)
        
        # Create clients
        self.data_api = DataAPI(proxy=self.account.proxy)
        self.clob_client = get_clob_client(self.account)
        
        # Load existing orders to exclude
        orders_count = self._load_existing_orders()
        positions_count = self._load_existing_positions()
        
        # Check initial balance
        initial_balance = self._get_usdc_balance()
        
        logger.info(
            f"Initialized: {orders_count} orders, {positions_count} positions excluded, balance=${initial_balance:.2f}",
            account=self.account.name,
            action="STRATEGY_READY",
            details={
                "excluded_tokens": len(self.excluded_tokens),
                "preset": self.preset.get('name', self.preset_name),
                "balance": initial_balance
            }
        )
        
        return True
    
    def _get_usdc_balance(self) -> float:
        """Get USDC balance for the account"""
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            collateral = self.clob_client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return float(collateral.get('balance', 0)) / 1e6
        except Exception as e:
            logger.warning(f"Balance check failed: {e}", account=self.account.name)
            return 0.0
    
    def _load_existing_orders(self) -> int:
        """Load existing orders into excluded set"""
        try:
            orders = self.clob_client.get_orders()
            count = 0
            for order in orders:
                token_id = order.get('asset_id', '')
                if token_id:
                    self.excluded_tokens.add(token_id)
                    self.placed_tokens.add(token_id)
                    count += 1
            return count
        except Exception as e:
            logger.error(f"Failed to load orders: {e}", account=self.account.name, exc_info=True)
            return 0
    
    def _load_existing_positions(self) -> int:
        """Load existing positions into excluded set"""
        try:
            wallet = self.account.proxy_wallet or self.clob_client.get_address()
            positions = self.data_api.get_all_positions(wallet)
            
            count = 0
            for pos in positions:
                token_id = pos.get('asset', '') or pos.get('tokenId', '')
                if token_id:
                    self.excluded_tokens.add(token_id)
                    count += 1
            
            return count
        except Exception as e:
            logger.error(f"Failed to load positions: {e}", account=self.account.name, exc_info=True)
            return 0
    
    def place_order(self, 
                   token_id: str, 
                   tick: float,
                   market_title: str = "",
                   outcome: str = "") -> bool:
        """
        Place a limit order at minimum tick price.
        
        Args:
            token_id: Token ID to buy
            tick: Tick size (minimum price)
            market_title: Market title for logging
            outcome: Outcome name for logging
            
        Returns:
            bool: True if order placed successfully
        """
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        
        start_time = time.time()
        
        # Use fixed price if set in preset (e.g., always 1 cent regardless of tick)
        fixed_price = self.preset.get('fixed_order_price', 0)
        if fixed_price > 0:
            price = fixed_price
            logger.debug(
                f"Using fixed_order_price=${fixed_price} (tick was ${tick})",
                account=self.account.name
            )
        else:
            price = tick
        
        # Use fixed size if set, otherwise calculate from order_amount
        fixed_size = self.preset.get('fixed_order_size', 0)
        if fixed_size > 0:
            size = fixed_size
            logger.debug(
                f"Using fixed_order_size={fixed_size} shares",
                account=self.account.name
            )
        else:
            size = self.preset.get('order_amount', 0.2) / price
        
        logger.info(
            f"📝 ORDER: price=${price:.4f}, size={size:.0f}, total=${price*size:.2f} | {market_title[:40]}",
            account=self.account.name,
            action="ORDER_PARAMS"
        )
        
        try:
            args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
            signed = self.clob_client.create_order(args)
            result = self.clob_client.post_order(signed, OrderType.GTC)
            
            duration_ms = (time.time() - start_time) * 1000
            
            if result.get('success', False) or result.get('orderID'):
                order_id = result.get('orderID', '')
                self.orders_placed += 1
                self.account.orders_placed += 1
                
                # Track in CSV
                self.trade_tracker.order_placed(
                    account=self.account.name,
                    token_id=token_id,
                    side="BUY",
                    price=price,
                    size=size,
                    order_id=order_id,
                    order_type="GTC",
                    market_title=market_title,
                    outcome=outcome,
                    duration_ms=duration_ms
                )
                
                logger.order_placed(
                    self.account.name, token_id, "BUY", 
                    price, size, order_id, duration_ms
                )
                
                return True
            else:
                error = result.get('errorMsg', result.get('error', str(result)))
                self.orders_failed += 1
                
                # Track failure
                self.trade_tracker.order_failed(
                    account=self.account.name,
                    token_id=token_id,
                    side="BUY",
                    price=price,
                    size=size,
                    error=error,
                    duration_ms=duration_ms
                )
                
                logger.order_failed(self.account.name, token_id, error, duration_ms)
                return False
                
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_str = str(e)
            
            # Don't log rate limits as errors
            if "429" not in error_str and "403" not in error_str:
                logger.error(
                    f"Order exception: {error_str}",
                    account=self.account.name,
                    exc_info=True
                )
            
            self.orders_failed += 1
            
            # Track failure
            self.trade_tracker.order_failed(
                account=self.account.name,
                token_id=token_id,
                side="BUY",
                price=tick,
                size=self.preset.get('order_amount', 0.2) / tick,
                error=error_str,
                duration_ms=duration_ms
            )
            
            return False
    
    def place_tiered_orders(self,
                           token_id: str,
                           tick: float,
                           tiers: list,
                           market_title: str = "",
                           outcome: str = "") -> dict:
        """
        Place multiple orders at different price tiers.
        
        Args:
            token_id: Token ID to buy
            tick: Market tick size (minimum price increment)
            tiers: List of dicts [{"price": 0.01, "size": 10}, ...]
            market_title: Market title for logging
            outcome: Outcome name for logging
            
        Returns:
            dict: {"placed": int, "failed": int, "skipped": int, "total_cost": float}
        """
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        
        result = {"placed": 0, "failed": 0, "skipped": 0, "total_cost": 0.0}
        
        for tier in tiers:
            price = tier['price']
            size = tier['size']
            
            # Skip tier if price is below market tick (can't place order below minimum)
            if price < tick:
                result['skipped'] += 1
                logger.debug(
                    f"⏭ TIER SKIP: ${price:.4f} < tick ${tick:.4f} | {market_title[:40]}",
                    account=self.account.name
                )
                continue
            
            # Check that price is a valid multiple of tick
            if tick > 0 and round(price / tick, 6) % 1 > 0.001:
                result['skipped'] += 1
                logger.debug(
                    f"⏭ TIER SKIP: ${price:.4f} not multiple of tick ${tick:.4f} | {market_title[:40]}",
                    account=self.account.name
                )
                continue
            
            start_time = time.time()
            
            logger.info(
                f"📝 TIER ORDER: ${price:.4f} × {size} shares = ${price*size:.2f} | {market_title[:40]}",
                account=self.account.name,
                action="ORDER_PARAMS"
            )
            
            try:
                args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
                signed = self.clob_client.create_order(args)
                api_result = self.clob_client.post_order(signed, OrderType.GTC)
                
                duration_ms = (time.time() - start_time) * 1000
                
                if api_result.get('success', False) or api_result.get('orderID'):
                    order_id = api_result.get('orderID', '')
                    result['placed'] += 1
                    result['total_cost'] += price * size
                    self.orders_placed += 1
                    self.account.orders_placed += 1
                    
                    self.trade_tracker.order_placed(
                        account=self.account.name,
                        token_id=token_id,
                        side="BUY",
                        price=price,
                        size=size,
                        order_id=order_id,
                        order_type="GTC",
                        market_title=market_title,
                        outcome=outcome,
                        duration_ms=duration_ms
                    )
                    
                    logger.order_placed(
                        self.account.name, token_id, "BUY",
                        price, size, order_id, duration_ms
                    )
                else:
                    error = api_result.get('errorMsg', api_result.get('error', str(api_result)))
                    result['failed'] += 1
                    self.orders_failed += 1
                    
                    self.trade_tracker.order_failed(
                        account=self.account.name,
                        token_id=token_id,
                        side="BUY",
                        price=price,
                        size=size,
                        error=error,
                        duration_ms=duration_ms
                    )
                    
                    logger.order_failed(self.account.name, token_id, error, duration_ms)
                    
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                error_str = str(e)
                
                if "429" not in error_str and "403" not in error_str:
                    logger.error(
                        f"Tier order exception: {error_str}",
                        account=self.account.name,
                        exc_info=True
                    )
                
                result['failed'] += 1
                self.orders_failed += 1
                
                self.trade_tracker.order_failed(
                    account=self.account.name,
                    token_id=token_id,
                    side="BUY",
                    price=price,
                    size=size,
                    error=error_str,
                    duration_ms=duration_ms
                )
            
            # Small delay between tier orders
            time.sleep(0.05)
        
        return result

    def place_sell_order(self, 
                        token_id: str, 
                        price: float,
                        size: float,
                        market_title: str = "",
                        outcome: str = "") -> bool:
        """
        Place a SELL limit order (take-profit).
        
        Args:
            token_id: Token ID to sell
            price: Sell price (e.g., 0.50 for 50 cents)
            size: Number of shares to sell
            market_title: Market title for logging
            outcome: Outcome name for logging
            
        Returns:
            bool: True if order placed successfully
        """
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL
        
        # Minimum order size is 5 shares
        if size < 5:
            logger.debug(
                f"SELL skipped: size {size:.0f} < 5 min | {market_title[:30]}",
                account=self.account.name
            )
            return False
        
        start_time = time.time()
        
        try:
            args = OrderArgs(price=price, size=size, side=SELL, token_id=token_id)
            signed = self.clob_client.create_order(args)
            result = self.clob_client.post_order(signed, OrderType.GTC)
            
            duration_ms = (time.time() - start_time) * 1000
            
            if result.get('success', False) or result.get('orderID'):
                order_id = result.get('orderID', '')
                self.sell_orders_placed += 1
                
                # Track in CSV
                self.trade_tracker.order_placed(
                    account=self.account.name,
                    token_id=token_id,
                    side="SELL",
                    price=price,
                    size=size,
                    order_id=order_id,
                    order_type="GTC",
                    market_title=market_title,
                    outcome=outcome,
                    duration_ms=duration_ms
                )
                
                logger.info(
                    f"📤 SELL ORDER: {market_title[:30]} | {outcome} | "
                    f"{size:.0f} shares @ ${price:.2f} = ${size*price:.2f}",
                    account=self.account.name,
                    action="SELL_ORDER_PLACED"
                )
                
                return True
            else:
                error = result.get('errorMsg', result.get('error', str(result)))
                logger.warning(
                    f"SELL order failed: {market_title[:30]} | {error}",
                    account=self.account.name,
                    action="SELL_ORDER_FAILED"
                )
                return False
                
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"SELL order exception: {e}",
                account=self.account.name,
                exc_info=True
            )
            return False
    
    def place_take_profit_orders(self) -> int:
        """
        Check positions and place SELL orders for take-profit.
        
        Strategy:
        - For each position with shares
        - If no SELL order exists for this token
        - Place SELL order for 50% of shares at 50 cents
        
        Returns:
            int: Number of take-profit orders placed
        """
        take_profit_price = self.preset.get('take_profit_price', 0.50)
        take_profit_ratio = self.preset.get('take_profit_ratio', 0.50)  # 50% of shares
        
        if not self.preset.get('auto_take_profit', True):
            return 0
        
        try:
            wallet = self.account.proxy_wallet or self.clob_client.get_address()
            positions = self.data_api.get_all_positions(wallet)
            
            if not positions:
                return 0
            
            # Get existing SELL orders to avoid duplicates
            existing_sells = set()
            try:
                orders = self.clob_client.get_orders()
                for order in orders:
                    if order.get('side') == 'SELL':
                        existing_sells.add(order.get('asset_id', ''))
            except:
                pass
            
            placed = 0
            
            for pos in positions:
                token_id = pos.get('asset', '') or pos.get('tokenId', '')
                if not token_id:
                    continue
                
                # Skip if already has take-profit order
                if token_id in self.take_profit_placed or token_id in existing_sells:
                    continue
                
                # Get position size
                size = float(pos.get('size', 0) or 0)
                if size < 5:  # Minimum order size is 5 shares
                    continue
                
                # Calculate sell size (50% of position)
                sell_size = int(size * take_profit_ratio)
                if sell_size < 5:  # Minimum order size is 5 shares
                    continue
                
                # Check if orderbook exists (market not closed/resolved)
                orderbook = self.data_api.get_orderbook(token_id)
                if not orderbook:
                    continue
                
                # Get market info for logging
                market_title = pos.get('title', pos.get('market', ''))[:50]
                outcome = pos.get('outcome', '')
                
                # Place the SELL order
                success = self.place_sell_order(
                    token_id=token_id,
                    price=take_profit_price,
                    size=sell_size,
                    market_title=market_title,
                    outcome=outcome
                )
                
                if success:
                    self.take_profit_placed.add(token_id)
                    placed += 1
                
                # Small delay between orders
                time.sleep(0.1)
            
            if placed > 0:
                logger.info(
                    f"🎯 Take-profit: {placed} SELL orders placed @ ${take_profit_price:.2f}",
                    account=self.account.name,
                    action="TAKE_PROFIT_BATCH"
                )
            
            return placed
            
        except Exception as e:
            logger.error(f"Take-profit error: {e}", account=self.account.name, exc_info=True)
            return 0
    
    def analyze_existing_positions_for_take_profit(self) -> tuple:
        """
        Analyze existing positions that don't have SELL orders.
        Returns tuple: (positions_list, skipped_closed, skipped_has_sell)
        
        Call this at startup to show user what positions can have TP placed.
        """
        take_profit_price = self.preset.get('take_profit_price', 0.50)
        take_profit_ratio = self.preset.get('take_profit_ratio', 0.50)
        
        positions_without_tp = []
        
        try:
            wallet = self.account.proxy_wallet or self.clob_client.get_address()
            positions = self.data_api.get_all_positions(wallet)
            
            if not positions:
                return []
            
            # Get existing SELL orders
            existing_sells = set()
            try:
                orders = self.clob_client.get_orders()
                for order in orders:
                    if order.get('side') == 'SELL':
                        existing_sells.add(order.get('asset_id', ''))
            except:
                pass
            
            skipped_no_orderbook = 0
            skipped_has_sell = 0
            
            for pos in positions:
                token_id = pos.get('asset', '') or pos.get('tokenId', '')
                if not token_id:
                    continue
                
                # Skip if already has SELL order
                if token_id in existing_sells:
                    skipped_has_sell += 1
                    continue
                
                size = float(pos.get('size', 0) or 0)
                if size < 5:  # Minimum order size is 5 shares
                    continue
                
                sell_size = int(size * take_profit_ratio)
                if sell_size < 5:  # Minimum order size is 5 shares
                    continue
                
                # Check if orderbook exists (market not closed/resolved)
                orderbook = self.data_api.get_orderbook(token_id)
                if not orderbook:
                    skipped_no_orderbook += 1
                    continue
                
                avg_price = float(pos.get('avgPrice', 0) or pos.get('avg_price', 0) or 0)
                market_title = pos.get('title', pos.get('market', ''))[:50]
                outcome = pos.get('outcome', '')
                
                # Calculate potential profit
                potential_revenue = sell_size * take_profit_price
                cost_basis = sell_size * avg_price
                potential_profit = potential_revenue - cost_basis
                
                positions_without_tp.append({
                    'token_id': token_id,
                    'market_title': market_title,
                    'outcome': outcome,
                    'size': size,
                    'sell_size': sell_size,
                    'avg_price': avg_price,
                    'take_profit_price': take_profit_price,
                    'potential_profit': potential_profit
                })
            
            if skipped_no_orderbook > 0 or skipped_has_sell > 0:
                logger.debug(
                    f"Skipped: {skipped_no_orderbook} closed, {skipped_has_sell} have SELL",
                    account=self.account.name
                )
            
            return positions_without_tp, skipped_no_orderbook, skipped_has_sell
            
        except Exception as e:
            logger.error(f"Analyze positions error: {e}", account=self.account.name, exc_info=True)
            return [], 0, 0
    
    def place_all_take_profits_silent(self) -> int:
        """
        Place take-profit orders for all positions without prompting.
        Called after user confirms in main.py.
        
        Returns:
            int: Number of take-profit orders placed
        """
        positions, _, _ = self.analyze_existing_positions_for_take_profit()
        
        if not positions:
            return 0
        
        take_profit_price = self.preset.get('take_profit_price', 0.50)
        placed = 0
        
        for pos in positions:
            success = self.place_sell_order(
                token_id=pos['token_id'],
                price=take_profit_price,
                size=pos['sell_size'],
                market_title=pos['market_title'],
                outcome=pos['outcome']
            )
            
            if success:
                self.take_profit_placed.add(pos['token_id'])
                placed += 1
            
            # Rate limiting: 0.15s between orders
            time.sleep(0.15)
        
        if placed > 0:
            logger.info(
                f"Placed {placed} take-profit SELL orders @ ${take_profit_price:.2f}",
                account=self.account.name,
                action="TP_PLACED"
            )
        
        return placed
    
    def reset_cache_if_needed(self):
        """Reset cache periodically to pick up new markets"""
        reset_interval = self.settings.cache_reset_minutes * 60
        
        if time.time() - self.last_reset > reset_interval:
            # Only reset placed_tokens, keep excluded_tokens intact
            self.placed_tokens.clear()
            self.placed_tokens.update(self.excluded_tokens)
            self.tick_cache.clear()
            self.last_reset = time.time()
            
            logger.debug(
                "Cache reset",
                account=self.account.name,
                action="CACHE_RESET",
                details={"excluded": len(self.excluded_tokens)}
            )
    
    def cancel_all_orders(self) -> Tuple[int, int]:
        """
        Cancel all open orders for this account.
        
        Returns:
            Tuple[int, int]: (cancelled_count, total_orders)
        """
        try:
            orders = self.clob_client.get_orders()
            total = len(orders)
            
            if total == 0:
                logger.info("No open orders to cancel", account=self.account.name, action="CANCEL_NONE")
                return 0, 0
            
            logger.info(f"Cancelling {total} orders...", account=self.account.name, action="CANCEL_START")
            
            result = self.clob_client.cancel_all()
            
            if result.get('canceled') or result.get('success') or 'not_canceled' in str(result):
                cancelled = total - len(result.get('not_canceled', []))
                logger.info(
                    f"Cancelled {cancelled}/{total} orders",
                    account=self.account.name,
                    action="CANCEL_COMPLETE"
                )
                return cancelled, total
            else:
                logger.warning(f"Cancel result: {result}", account=self.account.name)
                return 0, total
                
        except Exception as e:
            logger.error(f"Cancel all orders failed: {e}", account=self.account.name, exc_info=True)
            return 0, 0
    
    def cancel_orders_by_market(self, market_id: str = "", asset_id: str = "") -> Tuple[int, int]:
        """
        Cancel orders for a specific market or asset.
        
        Args:
            market_id: Market condition ID
            asset_id: Token ID
            
        Returns:
            Tuple[int, int]: (cancelled_count, total_matching)
        """
        try:
            result = self.clob_client.cancel_market_orders(market=market_id, asset_id=asset_id)
            
            cancelled = len(result.get('canceled', []))
            not_cancelled = len(result.get('not_canceled', []))
            
            logger.info(
                f"Market cancel: {cancelled} cancelled, {not_cancelled} failed",
                account=self.account.name,
                action="CANCEL_MARKET"
            )
            
            return cancelled, cancelled + not_cancelled
            
        except Exception as e:
            logger.error(f"Cancel market orders failed: {e}", account=self.account.name, exc_info=True)
            return 0, 0
    
    @abstractmethod
    def scan(self) -> Tuple[int, int]:
        """
        Scan markets and place orders.
        
        Returns:
            Tuple[int, int]: (orders_placed, candidates_found)
        """
        pass
    
    @abstractmethod
    def run(self):
        """Main strategy loop"""
        pass
