"""
Limit Sniper Strategy
====================
Fast order placement at minimum tick prices.
Optimized for speed and volume.

ENHANCED FEATURES:
- Multi-outcome skew detection (finds undervalued outcomes)
- Binary market dual-side betting (YES and NO when spread is large)
- Fair value calculation for repricing opportunities

Rate Limits (per IP/proxy):
- CLOB POST /order: 60/s sustained, 3500 burst/10s
- Tick Size: 20/s (200/10s)
- GAMMA /events: 50/s (500/10s)
"""

import json
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple, Any
from datetime import datetime, timezone, timedelta
from collections import deque

from .base import BaseStrategy, MarketCandidate
from core.logger import get_logger
from core.data_api import extract_markets_from_events

logger = get_logger()

# Rate limit delays (seconds)
RATE_LIMITS = {
    'order_delay': 0.1,       # 10 orders/sec per account (safe margin)
    'tick_size_delay': 0.05,  # 20 tick size requests/sec
    'scan_interval_min': 2.0, # Minimum between full scans
}

# Skew detection settings
SKEW_SETTINGS = {
    'multi_outcome_threshold': 0.5,    # Consider undervalued if price < fair_value * threshold
    'binary_spread_threshold': 0.90,   # Bet on both if YES + NO < this (10%+ spread)
    'min_skew_ratio': 2.0,             # Minimum fair_value / price ratio to consider
    'prioritize_skewed': True,         # Sort candidates by skew ratio
}


class RateLimiter:
    """
    Token bucket rate limiter for API requests.
    
    Tracks requests per second and enforces limits with backoff.
    """
    
    def __init__(self, max_per_second: float = 10.0, burst: int = 60, window_seconds: float = 10.0):
        """
        Args:
            max_per_second: Maximum sustained requests per second
            burst: Maximum burst capacity
            window_seconds: Window for tracking requests
        """
        self.max_per_second = max_per_second
        self.burst = burst
        self.window_seconds = window_seconds
        
        # Token bucket
        self.tokens = float(burst)
        self.max_tokens = float(burst)
        self.last_refill = time.time()
        
        # Request tracking for sliding window
        self.requests: deque = deque()  # Timestamps of recent requests
        
        # Backoff state
        self.backoff_until = 0.0
        self.consecutive_429s = 0
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.max_per_second)
        self.last_refill = now
        
        # Clean old requests from sliding window
        cutoff = now - self.window_seconds
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()
    
    def get_requests_in_window(self) -> int:
        """Get number of requests in the sliding window"""
        self._refill()
        return len(self.requests)
    
    def acquire(self) -> float:
        """
        Acquire permission to make a request.
        
        Returns:
            float: Time waited (0 if no wait needed)
        """
        waited = 0.0
        
        # Check backoff
        now = time.time()
        if now < self.backoff_until:
            wait = self.backoff_until - now
            time.sleep(wait)
            waited += wait
            now = time.time()
        
        self._refill()
        
        # Wait if no tokens available
        if self.tokens < 1:
            wait = (1 - self.tokens) / self.max_per_second
            time.sleep(wait)
            waited += wait
            self._refill()
        
        self.tokens -= 1
        self.requests.append(time.time())
        
        return waited
    
    def report_429(self):
        """Report a 429 rate limit error - triggers backoff"""
        self.consecutive_429s += 1
        
        # Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        backoff = min(30.0, 2 ** self.consecutive_429s)
        self.backoff_until = time.time() + backoff
        
        # Reduce token refill rate temporarily
        self.tokens = 0
        
        logger.warning(
            f"Rate limit 429 - backoff {backoff:.1f}s (consecutive: {self.consecutive_429s})",
            action="RATE_LIMIT_BACKOFF"
        )
    
    def report_success(self):
        """Report successful request - resets consecutive 429 counter"""
        if self.consecutive_429s > 0:
            self.consecutive_429s = max(0, self.consecutive_429s - 1)
    
    def get_current_rate(self) -> float:
        """Get current request rate (per second)"""
        self._refill()
        if len(self.requests) < 2:
            return 0.0
        return len(self.requests) / self.window_seconds


class LimitSniper(BaseStrategy):
    """
    Limit Sniper - places orders at minimum tick prices.
    
    Strategy:
    1. Scan all open markets
    2. Filter by preset criteria (price range, volume)
    3. Check tick size (min price increment)
    4. Place BUY orders at tick price
    
    Goal: High volume of small bets on low-probability outcomes.
    If any win, the payout is 100x+ the stake.
    
    Difference from Smart Sniper:
    - Limit Sniper: Max orders, minimal filtering (places on all that pass basic filters).
    - Smart Sniper: Quality > quantity (volume, liquidity, spread, activity).
    """
    
    def __init__(self, account, preset_name: str = "medium"):
        super().__init__(account, preset_name)
        
        # Sniper-specific stats
        self.markets_scanned = 0
        self.candidates_found = 0
        self.skipped_no_liquidity = 0
        self.skipped_blocked = 0
        self.skipped_tick = 0
        self.api_errors = 0
        
        # Skew detection stats
        self.skewed_markets_found = 0
        self.binary_dual_bets = 0
        self.multi_outcome_bets = 0
        
        # Timing
        self.last_scan_time = 0
        self.total_orders_session = 0
        self.session_start = time.time()
        
        # Rate limiter - 10 orders/sec sustained, 60 burst
        self.rate_limiter = RateLimiter(max_per_second=10.0, burst=60, window_seconds=10.0)
        
        # Minimum balance to continue trading
        self.min_balance_required = self.preset.get('order_amount', 0.2) * 10  # 10 orders worth
        self.last_balance_check = 0
        self.current_balance = 0.0
        
        # Skew detection settings (can be overridden by preset)
        self.skew_settings = {
            'multi_outcome_threshold': self.preset.get('multi_outcome_threshold', SKEW_SETTINGS['multi_outcome_threshold']),
            'binary_spread_threshold': self.preset.get('binary_spread_threshold', SKEW_SETTINGS['binary_spread_threshold']),
            'min_skew_ratio': self.preset.get('min_skew_ratio', SKEW_SETTINGS['min_skew_ratio']),
            'prioritize_skewed': self.preset.get('prioritize_skewed', SKEW_SETTINGS['prioritize_skewed']),
            'bet_both_sides': self.preset.get('bet_both_sides', True),  # Bet on YES and NO for binary markets
        }
    
    def _check_balance(self) -> float:
        """
        Check USDC balance for the account.
        
        Returns:
            float: USDC balance
        """
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            collateral = self.clob_client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance = float(collateral.get('balance', 0)) / 1e6
            self.current_balance = balance
            self.last_balance_check = time.time()
            return balance
        except Exception as e:
            logger.warning(f"Balance check failed: {e}", account=self.account.name)
            return self.current_balance
    
    def _analyze_market_skew(self, prices: List[float], tokens: List[str], outcomes: List[str], market: Dict) -> List[Dict]:
        """
        Analyze market for skew/undervaluation opportunities.
        
        For multi-outcome markets: Calculate fair value (1/N) and find undervalued outcomes.
        For binary markets: Check if both sides are cheap enough to bet on.
        
        Returns:
            List[Dict]: List of candidate tokens with skew analysis
        """
        candidates = []
        n_outcomes = len(prices)
        
        if n_outcomes < 2:
            return candidates
        
        max_ask = self.preset.get('max_ask', 0.10)
        min_ask = self.preset.get('min_ask', 0.001)
        
        # Calculate fair value (assuming equal probability)
        fair_value = 1.0 / n_outcomes
        
        if n_outcomes == 2:
            # Binary market - check for spread opportunity
            yes_price = prices[0] if len(prices) > 0 else 0.5
            no_price = prices[1] if len(prices) > 1 else 0.5
            total_price = yes_price + no_price
            
            # Large spread = opportunity to bet on both sides
            # If YES + NO < 0.90, there's 10%+ spread
            if self.skew_settings['bet_both_sides'] and total_price < self.skew_settings['binary_spread_threshold']:
                spread_pct = (1.0 - total_price) * 100
                
                for i, (token, price, outcome) in enumerate(zip(tokens, prices, outcomes)):
                    token = str(token).strip().strip('"')
                    if not token or len(token) < 10:
                        continue
                    
                    # Skip if already processed
                    if token in self.excluded_tokens or token in self.placed_tokens:
                        continue
                    
                    # Check if price is within range
                    if price < min_ask or price > max_ask:
                        continue
                    
                    # Calculate skew ratio (how undervalued)
                    skew_ratio = fair_value / price if price > 0 else 0
                    
                    candidates.append({
                        'token_id': token,
                        'market_id': market.get('id', ''),
                        'question': market.get('question', '')[:50],
                        'outcome': outcome,
                        'price': price,
                        'volume': float(market.get('volume', 0) or 0),
                        'fair_value': fair_value,
                        'skew_ratio': skew_ratio,
                        'spread_pct': spread_pct,
                        'is_binary_dual': True,
                        'market_type': 'binary'
                    })
                
                if len(candidates) == 2:
                    self.binary_dual_bets += 1
                    logger.debug(
                        f"🎯 Binary dual-bet opportunity: {market.get('question', '')[:40]} | "
                        f"YES=${yes_price:.3f} NO=${no_price:.3f} | Spread={spread_pct:.1f}%",
                        account=self.account.name,
                        action="BINARY_DUAL_BET"
                    )
            else:
                # Standard binary - just pick the cheaper side if it passes filters
                for i, (token, price, outcome) in enumerate(zip(tokens, prices, outcomes)):
                    token = str(token).strip().strip('"')
                    if not token or len(token) < 10:
                        continue
                    
                    if token in self.excluded_tokens or token in self.placed_tokens:
                        continue
                    
                    if price < min_ask or price > max_ask:
                        continue
                    
                    skew_ratio = fair_value / price if price > 0 else 0
                    
                    candidates.append({
                        'token_id': token,
                        'market_id': market.get('id', ''),
                        'question': market.get('question', '')[:50],
                        'outcome': outcome,
                        'price': price,
                        'volume': float(market.get('volume', 0) or 0),
                        'fair_value': fair_value,
                        'skew_ratio': skew_ratio,
                        'is_binary_dual': False,
                        'market_type': 'binary'
                    })
        else:
            # Multi-outcome market - find undervalued outcomes
            threshold = self.skew_settings['multi_outcome_threshold']
            min_skew = self.skew_settings['min_skew_ratio']
            
            for i, (token, price, outcome) in enumerate(zip(tokens, prices, outcomes)):
                token = str(token).strip().strip('"')
                if not token or len(token) < 10:
                    continue
                
                if token in self.excluded_tokens or token in self.placed_tokens:
                    continue
                
                if price < min_ask or price > max_ask:
                    continue
                
                # Calculate skew ratio
                skew_ratio = fair_value / price if price > 0 else 0
                
                # Check if significantly undervalued
                # Example: 8 outcomes, fair=12.5%, price=3% → skew_ratio=4.17x
                is_undervalued = price < (fair_value * threshold) and skew_ratio >= min_skew
                
                if is_undervalued:
                    self.multi_outcome_bets += 1
                    logger.debug(
                        f"🎯 Multi-outcome undervalued: {market.get('question', '')[:35]} | "
                        f"{outcome}=${price:.3f} (fair=${fair_value:.3f}, {skew_ratio:.1f}x)",
                        account=self.account.name,
                        action="MULTI_UNDERVALUED"
                    )
                
                candidates.append({
                    'token_id': token,
                    'market_id': market.get('id', ''),
                    'question': market.get('question', '')[:50],
                    'outcome': outcome,
                    'price': price,
                    'volume': float(market.get('volume', 0) or 0),
                    'fair_value': fair_value,
                    'skew_ratio': skew_ratio,
                    'is_undervalued': is_undervalued,
                    'market_type': f'multi_{n_outcomes}'
                })
        
        return candidates
    
    def _fetch_markets(self) -> List[Dict]:
        """
        Fetch all open markets from Gamma API.
        Uses pagination to get complete dataset.
        Supports API-level filtering via exclude_tag_ids and date filters.
        """
        fetch_start = time.time()
        
        # Calculate API-level filters if enabled
        end_date_min = None
        end_date_max = None
        exclude_tag_ids = None
        
        if self.preset.get('use_api_tag_filter', False):
            exclude_tag_ids = self.preset.get('exclude_tag_ids', [])
            
            # Calculate end_date_min (min_hours_to_end from now)
            min_hours = self.preset.get('min_hours_to_end', 0)
            if min_hours > 0:
                end_date_min = (datetime.now(timezone.utc) + timedelta(hours=min_hours)).isoformat()
            
            # Calculate end_date_max (max_days_to_end from now)
            max_days = self.preset.get('max_days_to_end', 0)
            if max_days > 0:
                end_date_max = (datetime.now(timezone.utc) + timedelta(days=max_days)).isoformat()
            
            if exclude_tag_ids:
                logger.debug(
                    f"API filter: exclude_tag_ids={exclude_tag_ids}",
                    account=self.account.name
                )
        
        events = self.data_api.get_all_events(
            closed=False,
            exclude_tag_ids=exclude_tag_ids,
            end_date_min=end_date_min,
            end_date_max=end_date_max,
            progress_callback=lambda msg: logger.debug(msg, account=self.account.name)
        )
        
        markets = extract_markets_from_events(events)
        
        fetch_duration = (time.time() - fetch_start) * 1000
        logger.debug(
            f"Fetched {len(events)} events, {len(markets)} markets",
            account=self.account.name,
            action="FETCH_MARKETS",
            duration_ms=fetch_duration
        )
        
        return markets
    
    def _get_tick_size(self, token_id: str) -> float:
        """Get tick size with caching"""
        if token_id in self.tick_cache:
            return self.tick_cache[token_id]
        
        tick = self.data_api.get_tick_size(token_id)
        self.tick_cache[token_id] = tick
        return tick
    
    def _get_tick_sizes_parallel(self, token_ids: List[str]) -> Dict[str, float]:
        """Get tick sizes for multiple tokens in parallel"""
        # Filter out cached
        to_fetch = [tid for tid in token_ids if tid not in self.tick_cache]
        
        if not to_fetch:
            return {tid: self.tick_cache[tid] for tid in token_ids}
        
        # Fetch in parallel
        with ThreadPoolExecutor(max_workers=self.settings.parallel_requests) as executor:
            results = list(executor.map(self._get_tick_size, to_fetch))
        
        # Combine with cached
        result = {}
        for tid in token_ids:
            if tid in self.tick_cache:
                result[tid] = self.tick_cache[tid]
            elif tid in to_fetch:
                idx = to_fetch.index(tid)
                result[tid] = results[idx]
                self.tick_cache[tid] = results[idx]
        
        return result
    
    def _check_sell_liquidity(self, token_id: str) -> Tuple[bool, str]:
        """Check if market has buyers for future selling"""
        if not self.settings.check_sell_liquidity:
            return True, "disabled"
        
        return self.data_api.check_sell_liquidity(
            token_id,
            min_bid_size=self.settings.min_bid_size,
            min_bid_count=self.settings.min_bid_count
        )
    
    def scan(self) -> Tuple[int, int]:
        """
        Scan markets and place orders.
        
        Returns:
            Tuple[int, int]: (orders_placed, candidates_found)
        """
        scan_start = time.time()
        
        # Reset cache if needed
        self.reset_cache_if_needed()
        
        # Refresh orders
        self._load_existing_orders()
        
        # Fetch markets
        markets = self._fetch_markets()
        self.markets_scanned = len(markets)
        
        if not markets:
            logger.info("⚠️ No markets fetched from API", account=self.account.name, action="NO_MARKETS")
            return 0, 0
        
        logger.debug(f"Fetched {len(markets)} markets", account=self.account.name)
        
        # Find candidates with skew analysis
        candidates = []
        binary_dual_count = 0
        multi_undervalued_count = 0
        
        for market in markets:
            # Apply market filter
            passes, reason = self.filter.filter_market(market)
            if not passes:
                if 'blocked' in reason:
                    self.skipped_blocked += 1
                continue
            
            # Parse prices
            prices = market.get('outcomePrices') or market.get('outcome_prices', '[]')
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    prices = []
            prices = [float(p) for p in prices] if prices else [0.5]
            
            # NOTE: We no longer skip "skewed" markets - we WANT them!
            # Skewed markets are where the opportunities are
            # if self.filter.is_skewed_market(prices):
            #     continue
            
            # Parse tokens
            tokens = market.get('clobTokenIds') or market.get('clob_token_ids', '')
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens) if tokens.startswith('[') else [t.strip() for t in tokens.split(',')]
                except:
                    tokens = []
            
            # Parse outcomes
            outcomes = market.get('outcomes', ['Yes', 'No'])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except:
                    outcomes = ['Yes', 'No']
            
            # Use new skew analysis method
            market_candidates = self._analyze_market_skew(prices, tokens, outcomes, market)
            
            # Track statistics
            for c in market_candidates:
                if c.get('is_binary_dual'):
                    binary_dual_count += 1
                if c.get('is_undervalued'):
                    multi_undervalued_count += 1
            
            candidates.extend(market_candidates)
        
        # Sort candidates: priority keywords first, then by skew ratio
        priority_keywords = self.preset.get('priority_keywords', [])
        if priority_keywords and candidates:
            def _priority_score(c):
                q = (c.get('question', '') + ' ' + c.get('outcome', '')).lower()
                for i, kw in enumerate(priority_keywords):
                    if kw.lower() in q:
                        return i  # Lower index = higher priority
                return len(priority_keywords)  # No match = lowest priority
            
            candidates.sort(key=lambda x: (_priority_score(x), -x.get('skew_ratio', 0)))
            
            # Log priority stats
            priority_count = sum(1 for c in candidates if _priority_score(c) < len(priority_keywords))
            if priority_count > 0:
                logger.info(
                    f"⚡ Priority: {priority_count} candidates matched priority keywords",
                    account=self.account.name,
                    action="PRIORITY_SORT"
                )
        elif self.skew_settings['prioritize_skewed'] and candidates:
            candidates.sort(key=lambda x: x.get('skew_ratio', 0), reverse=True)
        
        self.candidates_found = len(candidates)
        self.skewed_markets_found = binary_dual_count // 2 + multi_undervalued_count  # Approximate
        
        # Log skew statistics
        if binary_dual_count > 0 or multi_undervalued_count > 0:
            logger.info(
                f"🎯 Skew analysis: {binary_dual_count} binary dual-bets, "
                f"{multi_undervalued_count} multi-outcome undervalued",
                account=self.account.name,
                action="SKEW_ANALYSIS"
            )
        
        if not candidates:
            scan_duration = (time.time() - scan_start) * 1000
            logger.info(
                f"📊 Scan: {len(markets)} markets → 0 candidates "
                f"(cached={len(self.placed_tokens)}, excluded={len(self.excluded_tokens)})",
                account=self.account.name,
                action="NO_CANDIDATES"
            )
            return 0, 0
        
        # Get tick sizes in parallel
        batch_size = self.preset.get('batch_size', 100)
        batch = candidates[:batch_size]
        
        tick_sizes = self._get_tick_sizes_parallel([c['token_id'] for c in batch])
        
        # Place orders with rate limiting
        max_tick = self.preset.get('max_tick', 0.01)
        order_amount = self.preset.get('order_amount', 0.2)
        order_tiers = self.preset.get('order_tiers', [])
        fixed_order_price = self.preset.get('fixed_order_price', 0)
        fixed_order_size = self.preset.get('fixed_order_size', 0)
        use_tiers = len(order_tiers) > 0
        placed = 0
        skipped_tick = 0
        skipped_liq = 0
        errors = 0
        order_delay = RATE_LIMITS['order_delay']  # Rate limit: ~10 orders/sec
        
        # Show batch info
        if use_tiers:
            tier_desc = " | ".join([f"${t['price']}×{t['size']}" for t in order_tiers])
            logger.info(
                f"📋 Batch: {len(batch)} candidates | TIERS: {tier_desc}",
                account=self.account.name,
                action="BATCH_START"
            )
            # For tiered mode: highest tier price is the minimum tick we need
            min_tier_price = min(t['price'] for t in order_tiers)
            max_tier_price = max(t['price'] for t in order_tiers)
        elif fixed_order_price > 0:
            logger.info(
                f"📋 Batch: {len(batch)} candidates | Fixed price=${fixed_order_price} | "
                f"Size={fixed_order_size if fixed_order_size > 0 else order_amount/fixed_order_price:.0f} shares",
                account=self.account.name,
                action="BATCH_START"
            )
        else:
            logger.info(
                f"📋 Batch: {len(batch)} candidates | Tick≤${max_tick} | Amount=${order_amount}",
                account=self.account.name,
                action="BATCH_START"
            )
        
        for idx, candidate in enumerate(batch):
            token_id = candidate['token_id']
            tick = tick_sizes.get(token_id, 0.01)
            question = candidate['question'][:35]
            outcome = candidate['outcome']
            price = candidate['price']
            skew_ratio = candidate.get('skew_ratio', 0)
            fair_value = candidate.get('fair_value', 0.5)
            market_type = candidate.get('market_type', 'binary')
            is_dual_bet = candidate.get('is_binary_dual', False)
            
            # Tick validation
            if use_tiers:
                # Tiered mode: skip only if tick > highest tier price (no tier can be placed)
                if tick > max_tier_price:
                    self.placed_tokens.add(token_id)
                    skipped_tick += 1
                    self.skipped_tick += 1
                    logger.debug(
                        f"⏭ SKIP (tick=${tick:.3f}>${max_tier_price}): {question}",
                        account=self.account.name
                    )
                    continue
            elif fixed_order_price > 0:
                # Fixed price mode: skip if tick > fixed price (can't place below tick)
                if tick > fixed_order_price:
                    self.placed_tokens.add(token_id)
                    skipped_tick += 1
                    self.skipped_tick += 1
                    logger.debug(
                        f"⏭ SKIP (tick=${tick:.3f}>fixed ${fixed_order_price}): {question}",
                        account=self.account.name
                    )
                    continue
                # Check that fixed price is a valid multiple of tick
                if tick > 0 and round(fixed_order_price / tick, 6) % 1 > 0.001:
                    self.placed_tokens.add(token_id)
                    skipped_tick += 1
                    self.skipped_tick += 1
                    logger.debug(
                        f"⏭ SKIP (${fixed_order_price} not multiple of tick ${tick:.4f}): {question}",
                        account=self.account.name
                    )
                    continue
            else:
                # Standard mode: skip if tick too high
                if tick > max_tick:
                    self.placed_tokens.add(token_id)
                    skipped_tick += 1
                    self.skipped_tick += 1
                    logger.debug(
                        f"⏭ SKIP (tick=${tick:.3f}>{max_tick}): {question}",
                        account=self.account.name
                    )
                    continue
            
            # Check sell liquidity
            can_sell, reason = self._check_sell_liquidity(token_id)
            if not can_sell:
                self.placed_tokens.add(token_id)
                if reason in ['no_orderbook', 'no_bids']:
                    self.excluded_tokens.add(token_id)
                skipped_liq += 1
                self.skipped_no_liquidity += 1
                logger.debug(
                    f"⏭ SKIP (liq={reason}): {question}",
                    account=self.account.name
                )
                continue
            
            # Place order(s) with rate limiting
            try:
                # Acquire rate limit token (may wait if rate exceeded)
                waited = self.rate_limiter.acquire()
                if waited > 0.1:
                    logger.debug(f"Rate limited, waited {waited:.2f}s", account=self.account.name)
                
                if use_tiers:
                    # === TIERED ORDER MODE ===
                    tier_result = self.place_tiered_orders(
                        token_id=token_id,
                        tick=tick,
                        tiers=order_tiers,
                        market_title=candidate['question'],
                        outcome=candidate['outcome']
                    )
                    
                    tier_placed = tier_result['placed']
                    tier_failed = tier_result['failed']
                    tier_skipped = tier_result['skipped']
                    tier_cost = tier_result['total_cost']
                    
                    if tier_placed > 0:
                        self.placed_tokens.add(token_id)
                        self.excluded_tokens.add(token_id)
                        placed += 1
                        self.total_orders_session += tier_placed
                        self.rate_limiter.report_success()
                        
                        # Calculate total shares placed across tiers
                        total_shares = sum(
                            t['size'] for t in order_tiers 
                            if t['price'] >= tick and (tick <= 0 or round(t['price'] / tick, 6) % 1 <= 0.001)
                        )
                        potential_win = total_shares
                        potential_profit = potential_win - tier_cost
                        
                        skew_info = ""
                        if skew_ratio > 1.5:
                            skew_info = f" | 🎯 Skew: {skew_ratio:.1f}x"
                        if is_dual_bet:
                            skew_info += " | 🔀 DUAL-BET"
                        
                        logger.info(
                            f"✅ TIERED #{self.total_orders_session}: {question} | "
                            f"{outcome} | {tier_placed} tiers (skip {tier_skipped}) "
                            f"cost=${tier_cost:.2f} | tick=${tick:.4f} | "
                            f"Potential: ${potential_profit:.2f}{skew_info}",
                            account=self.account.name,
                            action="ORDER_PLACED"
                        )
                    else:
                        self.placed_tokens.add(token_id)
                        errors += tier_failed
                        self.api_errors += tier_failed
                        if tier_failed == 0 and tier_skipped > 0:
                            skipped_tick += 1
                            self.skipped_tick += 1
                else:
                    # === SINGLE ORDER MODE (legacy) ===
                    success = self.place_order(
                        token_id=token_id,
                        tick=tick,
                        market_title=candidate['question'],
                        outcome=candidate['outcome']
                    )
                    
                    if success:
                        self.placed_tokens.add(token_id)
                        self.excluded_tokens.add(token_id)
                        placed += 1
                        self.total_orders_session += 1
                        self.rate_limiter.report_success()
                        
                        # Potential profit calculation
                        actual_price = fixed_order_price if fixed_order_price > 0 else tick
                        actual_size = fixed_order_size if fixed_order_size > 0 else order_amount / actual_price
                        actual_cost = actual_price * actual_size
                        potential_win = actual_size
                        potential_profit = potential_win - actual_cost
                        
                        skew_info = ""
                        if skew_ratio > 1.5:
                            skew_info = f" | 🎯 Skew: {skew_ratio:.1f}x (fair=${fair_value:.3f})"
                        if is_dual_bet:
                            skew_info += " | 🔀 DUAL-BET"
                        
                        logger.info(
                            f"✅ ORDER #{self.total_orders_session}: {question} | "
                            f"{outcome} @ ${actual_price:.4f} × {actual_size:.0f} shares | "
                            f"Potential: ${potential_profit:.2f} ({potential_win/actual_cost:.0f}x){skew_info}",
                            account=self.account.name,
                            action="ORDER_PLACED"
                        )
                    else:
                        self.placed_tokens.add(token_id)
                        errors += 1
                        self.api_errors += 1
            except Exception as e:
                errors += 1
                self.api_errors += 1
                error_msg = str(e)[:50]
                logger.warning(f"❌ Order error: {error_msg}", account=self.account.name)
                
                if "429" in str(e) or "rate" in str(e).lower():
                    # Rate limited - trigger backoff
                    self.rate_limiter.report_429()
                elif "Request exception" in str(e):
                    # Network error - short pause
                    time.sleep(2)
            
            # Minimal delay between orders (rate limiter handles the rest)
            time.sleep(0.05)
        
        scan_duration = (time.time() - scan_start) * 1000
        self.last_scan_time = time.time()
        
        # Detailed logging with skew info
        session_time = (time.time() - self.session_start) / 60
        orders_per_min = self.total_orders_session / max(1, session_time)
        
        # Count dual-bets and undervalued in this batch
        batch_dual = sum(1 for c in batch if c.get('is_binary_dual', False))
        batch_undervalued = sum(1 for c in batch if c.get('is_undervalued', False))
        
        skew_info = ""
        if batch_dual > 0 or batch_undervalued > 0:
            skew_info = f" | 🎯 Skew: dual={batch_dual}, underval={batch_undervalued}"
        
        logger.info(
            f"Scan: {len(markets)} mkts → {len(candidates)} cand → {placed} orders "
            f"(skip: tick={skipped_tick}, liq={skipped_liq}, err={errors}){skew_info} "
            f"[Session: {self.total_orders_session} orders, {orders_per_min:.1f}/min]",
            account=self.account.name,
            action="SCAN_COMPLETE"
        )
        
        # Place take-profit SELL orders for filled positions (check every scan)
        if self.preset.get('auto_take_profit', True):
            tp_placed = self.place_take_profit_orders()
            if tp_placed > 0:
                logger.info(
                    f"🎯 Take-profit: {tp_placed} SELL orders @ ${self.preset.get('take_profit_price', 0.50):.2f}",
                    account=self.account.name,
                    action="TAKE_PROFIT"
                )
        
        return placed, len(candidates)
    
    def run(self):
        """Main sniper loop"""
        if not self.init():
            logger.error("Initialization failed", account=self.account.name)
            return
        
        scan_interval = max(RATE_LIMITS['scan_interval_min'], self.preset.get('scan_interval', 2.0))
        no_candidates_pause = self.settings.no_candidates_pause_minutes * 60
        order_amount = self.preset.get('order_amount', 0.2)
        max_tick = self.preset.get('max_tick', 0.01)
        min_price = self.preset.get('min_price', 0.0)
        max_price = self.preset.get('max_price', 0.10)
        batch_size = self.preset.get('batch_size', 100)
        
        # Calculate potential returns
        max_multiplier = 1 / max_tick if max_tick > 0 else 100
        potential_profit = order_amount * (max_multiplier - 1)
        
        # Print detailed startup info
        print(f"\n{'═'*70}")
        print(f"  🎯 LIMIT SNIPER - {self.account.name}")
        print(f"{'═'*70}")
        print(f"""
  ╔════════════════════════════════════════════════════════════════╗
  ║                      TRADING PARAMETERS                        ║
  ╠════════════════════════════════════════════════════════════════╣
  ║  Order amount:     ${order_amount:<10}                           ║
  ║  Price range:      ${min_price:.2f} - ${max_price:.2f}                          ║
  ║  Max tick:         ${max_tick} (min order price)                  ║
  ║  Batch size:       {batch_size} candidates per scan               ║
  ╠════════════════════════════════════════════════════════════════╣
  ║                      PROFIT POTENTIAL                           ║
  ╠════════════════════════════════════════════════════════════════╣
  ║  If win:           ${order_amount} -> ${order_amount/max_tick:.2f} (x{max_multiplier:.0f})             ║
  ║  Net profit:       ${potential_profit:.2f} per win                        ║
  ║  Break-even:       1 in {max_multiplier:.0f} bets                          ║
  ╠════════════════════════════════════════════════════════════════╣
  ║                      RATE LIMITS                               ║
  ╠════════════════════════════════════════════════════════════════╣
  ║  Orders/sec:       ~{1/RATE_LIMITS['order_delay']:.0f} (delay: {RATE_LIMITS['order_delay']*1000:.0f}ms)                    ║
  ║  Scan interval:    {scan_interval}s                                     ║
  ║  Pause if no cand: {no_candidates_pause//60} min                               ║
  ╚════════════════════════════════════════════════════════════════╝
""")
        # Show skew detection settings
        bet_both = self.skew_settings.get('bet_both_sides', True)
        spread_thresh = self.skew_settings.get('binary_spread_threshold', 0.90)
        multi_thresh = self.skew_settings.get('multi_outcome_threshold', 0.5)
        
        print(f"""  ╔════════════════════════════════════════════════════════════════╗
  ║                      🎯 SKEW DETECTION (NEW!)                   ║
  ╠════════════════════════════════════════════════════════════════╣
  ║  Dual-Bet (YES+NO):  {'ON' if bet_both else 'OFF':<10}                             ║
  ║  Binary spread:      <{spread_thresh:.0%} -> bet BOTH outcomes            ║
  ║  Multi-outcome:      <{multi_thresh:.0%} of fair value -> undervalued       ║
  ║  Prioritize skewed:  {'YES' if self.skew_settings['prioritize_skewed'] else 'NO':<10}                             ║
  ╚════════════════════════════════════════════════════════════════╝
""")
        print(f"  Logic:")
        print(f"     1. Scan all open markets")
        print(f"     2. Filter by price (${min_price:.2f}-${max_price:.2f})")
        print(f"     3. Skew: Binary YES+NO < {spread_thresh:.0%} -> bet both; multi < fair*{multi_thresh} -> undervalued")
        print(f"     4. Check tick size (<= ${max_tick})")
        print(f"     5. Check liquidity")
        print(f"     6. Place BUY at tick price")
        print(f"\n  Goal: catch repricing; 1 win -> profit x{max_multiplier:.0f}")
        print(f"\n{'═'*70}\n")
        
        logger.info(
            f"Starting sniper: amount=${order_amount}, tick=${max_tick}, interval={scan_interval}s",
            account=self.account.name,
            action="SNIPER_START"
        )
        
        consecutive_empty = 0
        last_stats_time = time.time()
        last_status_time = time.time()
        balance_check_interval = 300  # Check balance every 5 minutes
        
        while self.running:
            try:
                self.cycle += 1
                
                # Check balance periodically (every 5 minutes)
                if time.time() - self.last_balance_check > balance_check_interval:
                    balance = self._check_balance()
                    
                    if balance < self.min_balance_required:
                        logger.warning(
                            f"⚠️ Insufficient balance: ${balance:.2f} < ${self.min_balance_required:.2f} - pausing 5 min",
                            account=self.account.name,
                            action="INSUFFICIENT_BALANCE"
                        )
                        print(f"\n[{self.account.name}] Balance ${balance:.2f} < ${self.min_balance_required:.2f} - pausing 5 min\n")
                        time.sleep(300)  # Wait 5 minutes before checking again
                        continue
                
                placed, candidates = self.scan()
                
                # Periodic status every 2 minutes (console log)
                if time.time() - last_status_time > 120:
                    session_mins = (time.time() - self.session_start) / 60
                    logger.info(
                        f"🔄 Status: cycle={self.cycle}, orders={self.total_orders_session}, "
                        f"cached={len(self.placed_tokens)}, time={session_mins:.1f}min",
                        account=self.account.name,
                        action="STATUS"
                    )
                    last_status_time = time.time()
                
                # Print detailed stats (every 5 minutes)
                if time.time() - last_stats_time > 300:
                    self._print_stats()
                    last_stats_time = time.time()
                
                # Handle empty results
                if candidates == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 10:
                        logger.info(
                            f"Pausing {no_candidates_pause//60} min - no candidates",
                            account=self.account.name,
                            action="SNIPER_PAUSE"
                        )
                        time.sleep(no_candidates_pause)
                        consecutive_empty = 0
                        
                        # Reload after pause
                        self._load_existing_orders()
                        self._load_existing_positions()
                else:
                    consecutive_empty = 0
                
                # Rate limit: ensure minimum interval between scans
                time.sleep(scan_interval)
                
            except KeyboardInterrupt:
                self._print_stats()
                logger.info(
                    f"Stopped. Total orders: {self.total_orders_session}",
                    account=self.account.name,
                    action="SNIPER_STOP"
                )
                self.running = False
                break
                
            except Exception as e:
                self.api_errors += 1
                logger.exception(f"Scan error: {e}", account=self.account.name)
                
                # If rate limited, use rate limiter backoff
                if "rate" in str(e).lower() or "429" in str(e):
                    self.rate_limiter.report_429()
                else:
                    time.sleep(5)
    
    def _print_stats(self):
        """Print detailed session statistics"""
        session_time = (time.time() - self.session_start) / 60
        orders_per_min = self.total_orders_session / max(1, session_time)
        order_amount = self.preset.get('order_amount', 0.2)
        max_tick = self.preset.get('max_tick', 0.01)
        
        total_invested = self.total_orders_session * order_amount
        max_multiplier = 1 / max_tick if max_tick > 0 else 100
        potential_max_win = order_amount * max_multiplier
        
        # Efficiency metrics
        total_checked = self.skipped_tick + self.skipped_no_liquidity + self.total_orders_session
        order_rate = (self.total_orders_session / max(1, total_checked)) * 100 if total_checked > 0 else 0
        
        print(f"\n{'═'*60}")
        print(f"  SESSION STATS - {self.account.name}")
        print(f"{'═'*60}")
        print(f"""
  ╔════════════════════════════════════════════════════════╗
  ║                    TIME                                 ║
  ╠════════════════════════════════════════════════════════╣
  ║  Uptime:              {session_time:>6.1f} min                    ║
  ║  Scan cycles:         {self.cycle:>5}                        ║
  ╠════════════════════════════════════════════════════════╣
  ║                    ORDERS                              ║
  ╠════════════════════════════════════════════════════════╣
  ║  Placed:              {self.total_orders_session:>6} orders                ║
  ║  Rate:                {orders_per_min:>6.1f} orders/min              ║
  ║  Success rate:       {order_rate:>6.1f}%                         ║
  ╠════════════════════════════════════════════════════════╣
  ║                    INVESTED                             ║
  ╠════════════════════════════════════════════════════════╣
  ║  Total invested:      ${total_invested:>7.2f}                       ║
  ║  Per order:           ${order_amount:>7.2f}                       ║
  ║  Max win:             ${potential_max_win:>7.2f} (x{max_multiplier:.0f})              ║
  ╠════════════════════════════════════════════════════════╣
  ║                    SKEW DETECTION                       ║
  ╠════════════════════════════════════════════════════════╣
  ║  Skewed markets:      {self.skewed_markets_found:>6}                          ║
  ║  Binary dual-bets:    {self.binary_dual_bets:>6}                          ║
  ║  Multi undervalued:   {self.multi_outcome_bets:>6}                          ║
  ╠════════════════════════════════════════════════════════╣
  ║                    SKIPPED                             ║
  ╠════════════════════════════════════════════════════════╣
  ║  High tick:           {self.skipped_tick:>6} markets                 ║
  ║  No liquidity:        {self.skipped_no_liquidity:>6} markets                 ║
  ║  Blocked:             {self.skipped_blocked:>6} markets                 ║
  ║  API errors:          {self.api_errors:>6}                          ║
  ╠════════════════════════════════════════════════════════╣
  ║                    SCAN                                ║
  ╠════════════════════════════════════════════════════════╣
  ║  In cache:            {len(self.placed_tokens):>6}                          ║
  ║  Excluded:            {len(self.excluded_tokens):>6}                          ║
  ╠════════════════════════════════════════════════════════╣
  ║                    BALANCE                              ║
  ╠════════════════════════════════════════════════════════╣
  ║  Current:             ${self.current_balance:>7.2f}                       ║
  ║  Min required:        ${self.min_balance_required:>7.2f}                       ║
  ║  Rate (orders/sec):  {self.rate_limiter.get_current_rate():>7.1f}                       ║
  ╚════════════════════════════════════════════════════════╝
""")
        print(f"{'═'*60}\n")
