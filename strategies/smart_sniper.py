"""
Smart Sniper Strategy
====================
Intelligent market selection with advanced filters:
- Market quality scoring
- Liquidity analysis
- Spread analysis
- Historical performance

ENHANCED FEATURES (same as LimitSniper):
- Multi-outcome skew detection (finds undervalued outcomes)
- Binary market dual-side betting (YES and NO when spread is large)
- Fair value calculation for repricing opportunities
"""

import json
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple, Any
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from .base import BaseStrategy, MarketCandidate
from core.logger import get_logger
from core.data_api import extract_markets_from_events

logger = get_logger()

# Skew detection settings (shared with LimitSniper)
SKEW_SETTINGS = {
    'multi_outcome_threshold': 0.5,    # Consider undervalued if price < fair_value * threshold
    'binary_spread_threshold': 0.90,   # Bet on both if YES + NO < this (10%+ spread)
    'min_skew_ratio': 2.0,             # Minimum fair_value / price ratio to consider
    'prioritize_skewed': True,         # Sort candidates by skew ratio
}


@dataclass
class MarketScore:
    """Market quality score"""
    token_id: str
    total_score: float
    volume_score: float
    liquidity_score: float
    spread_score: float
    timing_score: float
    activity_score: float
    reasons: List[str]


class SmartSniper(BaseStrategy):
    """
    Smart Sniper - quality over quantity.
    
    Advanced filtering:
    1. Volume and liquidity thresholds
    2. Spread analysis (too wide = bad)
    3. Order book depth
    4. Market activity
    5. Time to expiration
    
    Goal: Find markets with real trading opportunity,
    not just spam random bets.
    """
    
    def __init__(self, account, preset_name: str = "smart"):
        super().__init__(account, preset_name)
        
        # Smart-specific settings
        self.min_score = self.preset.get('min_score', 50)
        self.require_sell_liquidity = self.preset.get('require_sell_liquidity', True)
        self.min_bid_depth = self.preset.get('min_bid_depth', 3)
        self.min_spread_pct = self.preset.get('min_spread_pct', 0.02)
        self.max_spread_pct = self.preset.get('max_spread_pct', 0.50)
        
        # Scoring weights
        self.weights = {
            'volume': 0.25,
            'liquidity': 0.25,
            'spread': 0.20,
            'timing': 0.15,
            'activity': 0.15
        }
        
        # Stats
        self.scored_markets: List[MarketScore] = []
        
        # Skew detection stats
        self.skewed_markets_found = 0
        self.binary_dual_bets = 0
        self.multi_outcome_bets = 0
        
        # Skew detection settings (can be overridden by preset)
        self.skew_settings = {
            'multi_outcome_threshold': self.preset.get('multi_outcome_threshold', SKEW_SETTINGS['multi_outcome_threshold']),
            'binary_spread_threshold': self.preset.get('binary_spread_threshold', SKEW_SETTINGS['binary_spread_threshold']),
            'min_skew_ratio': self.preset.get('min_skew_ratio', SKEW_SETTINGS['min_skew_ratio']),
            'prioritize_skewed': self.preset.get('prioritize_skewed', SKEW_SETTINGS['prioritize_skewed']),
            'bet_both_sides': self.preset.get('bet_both_sides', True),
        }
    
    def _analyze_market_skew(self, prices: List[float], tokens: List[str], outcomes: List[str], market: Dict) -> List[Dict]:
        """
        Analyze market for skew/undervaluation opportunities.
        Same logic as LimitSniper.
        
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
            if self.skew_settings['bet_both_sides'] and total_price < self.skew_settings['binary_spread_threshold']:
                spread_pct = (1.0 - total_price) * 100
                
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
                        'liquidity': float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0),
                        'end_date': market.get('endDate') or market.get('end_date_iso'),
                        'fair_value': fair_value,
                        'skew_ratio': skew_ratio,
                        'spread_pct': spread_pct,
                        'is_binary_dual': True,
                        'market_type': 'binary'
                    })
                
                if len(candidates) == 2:
                    self.binary_dual_bets += 1
                    logger.debug(
                        f"🎯 Binary dual-bet: {market.get('question', '')[:40]} | "
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
                        'liquidity': float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0),
                        'end_date': market.get('endDate') or market.get('end_date_iso'),
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
                
                skew_ratio = fair_value / price if price > 0 else 0
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
                    'liquidity': float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0),
                    'end_date': market.get('endDate') or market.get('end_date_iso'),
                    'fair_value': fair_value,
                    'skew_ratio': skew_ratio,
                    'is_undervalued': is_undervalued,
                    'market_type': f'multi_{n_outcomes}'
                })
        
        return candidates
    
    def _score_market(self, market: Dict, book_data: Optional[Dict]) -> Optional[MarketScore]:
        """
        Score a market based on multiple factors.
        
        Returns:
            MarketScore or None if market doesn't qualify
        """
        reasons = []
        scores = {}
        
        # ===== VOLUME SCORE (0-100) =====
        volume = float(market.get('volume', 0) or 0)
        min_vol = max(1, self.preset.get('min_volume', 25000))  # Prevent division by zero
        
        if volume < min_vol:
            return None
        
        # Score: 50 at min_volume, 100 at 5x min_volume
        divisor = 4 * min_vol
        volume_score = min(100, 50 + 50 * (volume - min_vol) / divisor) if divisor > 0 else 50
        scores['volume'] = volume_score
        reasons.append(f"vol=${volume/1000:.0f}k")
        
        # ===== LIQUIDITY SCORE (0-100) =====
        liquidity = float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0)
        min_liq = max(1, self.preset.get('min_liquidity', 300))  # Prevent division by zero
        
        if self.preset.get('require_liquidity', True) and liquidity < min_liq:
            return None
        
        liq_divisor = 4 * min_liq
        liquidity_score = min(100, 50 + 50 * (liquidity - min_liq) / liq_divisor) if liq_divisor > 0 and liquidity >= min_liq else 0
        scores['liquidity'] = liquidity_score
        reasons.append(f"liq=${liquidity:.0f}")
        
        # ===== SPREAD SCORE (0-100) =====
        spread_score = 50  # Default
        
        if book_data:
            spread_pct = book_data.get('spread_pct', 0.5) or 0.5
            
            # Too tight spread = maybe manipulated
            if spread_pct < self.min_spread_pct:
                spread_score = 30
                reasons.append(f"spread_tight={spread_pct:.1%}")
            # Too wide spread = illiquid
            elif spread_pct > self.max_spread_pct:
                spread_score = 20
                reasons.append(f"spread_wide={spread_pct:.1%}")
            else:
                # Ideal spread: 2-20%, score 60-100
                spread_score = max(0, min(100, 100 - (spread_pct * 200)))
                reasons.append(f"spread={spread_pct:.1%}")
            
            # Bid depth check
            bid_depth = float(book_data.get('bid_depth', 0) or 0)
            if bid_depth < self.min_bid_depth:
                spread_score *= 0.5
                reasons.append(f"bid_shallow={bid_depth:.1f}")
            else:
                reasons.append(f"bid_depth={bid_depth:.1f}")
        
        scores['spread'] = spread_score
        
        # ===== TIMING SCORE (0-100) =====
        end_date = market.get('endDate') or market.get('end_date_iso')
        timing_score = 50  # Default
        
        if end_date:
            try:
                if isinstance(end_date, str):
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                else:
                    end_dt = datetime.fromtimestamp(end_date, tz=timezone.utc)
                
                hours_until = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                days_until = hours_until / 24
                
                # Ideal: 3-14 days out
                if 3 <= days_until <= 14:
                    timing_score = 100
                elif 1 <= days_until < 3:
                    timing_score = 70  # A bit soon
                elif 14 < days_until <= 30:
                    timing_score = 80  # A bit far
                elif days_until < 1:
                    timing_score = 20  # Too soon
                else:
                    timing_score = 40  # Too far
                
                reasons.append(f"days={days_until:.0f}")
                
            except:
                pass
        
        scores['timing'] = timing_score
        
        # ===== ACTIVITY SCORE (0-100) =====
        # Based on recent trading activity
        activity_score = 50
        
        # Check if market has recent activity indicators
        if book_data:
            bid_count = book_data.get('bid_count', 0)
            ask_count = book_data.get('ask_count', 0)
            total_orders = bid_count + ask_count
            
            if total_orders >= 10:
                activity_score = 100
            elif total_orders >= 5:
                activity_score = 75
            elif total_orders >= 2:
                activity_score = 50
            else:
                activity_score = 25
            
            reasons.append(f"orders={total_orders}")
        
        scores['activity'] = activity_score
        
        # ===== CALCULATE TOTAL SCORE =====
        total_score = sum(
            scores[key] * self.weights[key] 
            for key in self.weights
        )
        
        return MarketScore(
            token_id=market.get('token_id', ''),
            total_score=total_score,
            volume_score=scores['volume'],
            liquidity_score=scores['liquidity'],
            spread_score=scores['spread'],
            timing_score=scores['timing'],
            activity_score=scores['activity'],
            reasons=reasons
        )
    
    def _analyze_candidate(self, candidate: Dict) -> Tuple[Optional[MarketScore], Dict]:
        """Analyze a candidate market"""
        try:
            token_id = candidate['token_id']
            
            # Get order book data
            book_data = self.data_api.get_market_spread(token_id)
            
            # Create market dict for scoring
            market_data = {
                'token_id': token_id,
                'volume': candidate.get('volume', 0) or 0,
                'liquidity': candidate.get('liquidity', 0) or 0,
                'endDate': candidate.get('end_date'),
                **candidate
            }
            
            score = self._score_market(market_data, book_data)
            
            return score, book_data or {}
        except Exception as e:
            # Return None score on any error
            logger.debug(f"Analyze error for {candidate.get('token_id', 'unknown')[:20]}: {e}")
            return None, {}
    
    def scan(self) -> Tuple[int, int]:
        """
        Smart scan - analyze and score markets before placing orders.
        """
        scan_start = time.time()
        
        # Reset cache if needed
        self.reset_cache_if_needed()
        
        # Refresh orders
        self._load_existing_orders()
        
        logger.debug(f"Fetching markets...", account=self.account.name)
        
        # Calculate date filters based on preset
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
        
        # Fetch markets with API-level filtering
        events = self.data_api.get_all_events(
            closed=False,
            exclude_tag_ids=exclude_tag_ids,
            end_date_min=end_date_min,
            end_date_max=end_date_max
        )
        markets = extract_markets_from_events(events)
        
        if not markets:
            logger.info(f"⚠️ No markets fetched from API", account=self.account.name, action="NO_MARKETS")
            return 0, 0
        
        logger.debug(f"Fetched {len(events)} events, {len(markets)} markets", account=self.account.name)
        
        # First pass: basic filtering with skew analysis
        pre_candidates = []
        binary_dual_count = 0
        multi_undervalued_count = 0
        
        for market in markets:
            passes, reason = self.filter.filter_market(market)
            if not passes:
                continue
            
            # Parse tokens and prices
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
            
            tokens = market.get('clobTokenIds') or market.get('clob_token_ids', '')
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens) if tokens.startswith('[') else [t.strip() for t in tokens.split(',')]
                except:
                    tokens = []
            
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
            
            pre_candidates.extend(market_candidates)
        
        # Sort by skew ratio if enabled (most undervalued first)
        if self.skew_settings['prioritize_skewed'] and pre_candidates:
            pre_candidates.sort(key=lambda x: x.get('skew_ratio', 0), reverse=True)
        
        self.skewed_markets_found = binary_dual_count // 2 + multi_undervalued_count
        
        # Log skew statistics
        if binary_dual_count > 0 or multi_undervalued_count > 0:
            logger.info(
                f"🎯 Skew analysis: {binary_dual_count} binary dual-bets, "
                f"{multi_undervalued_count} multi-outcome undervalued",
                account=self.account.name,
                action="SKEW_ANALYSIS"
            )
        
        if not pre_candidates:
            logger.info(
                f"📊 Scan: {len(markets)} markets → 0 pre-candidates (all filtered/cached)",
                account=self.account.name,
                action="NO_CANDIDATES"
            )
            return 0, 0
        
        logger.debug(f"Pre-candidates: {len(pre_candidates)}", account=self.account.name)
        
        # Second pass: score candidates
        batch_size = min(self.preset.get('batch_size', 80), len(pre_candidates))
        batch = pre_candidates[:batch_size]
        
        scored_candidates = []
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(self._analyze_candidate, batch))
        
        for candidate, (score, book_data) in zip(batch, results):
            if score is None:
                self.placed_tokens.add(candidate['token_id'])
                continue
            
            if score.total_score < self.min_score:
                self.placed_tokens.add(candidate['token_id'])
                continue
            
            # Check sell liquidity if required
            if self.require_sell_liquidity:
                bid_depth = book_data.get('bid_depth', 0)
                if bid_depth < self.min_bid_depth:
                    self.placed_tokens.add(candidate['token_id'])
                    continue
            
            candidate['score'] = score
            candidate['book_data'] = book_data
            scored_candidates.append(candidate)
        
        # Sort by score (highest first)
        scored_candidates.sort(key=lambda x: x['score'].total_score, reverse=True)
        
        # Log results
        if scored_candidates:
            top_info = [f"{c['question'][:20]}={c['score'].total_score:.0f}" for c in scored_candidates[:5]]
            logger.info(
                f"📊 Scan: {len(markets)} mkts → {len(pre_candidates)} pre → {len(scored_candidates)} scored",
                account=self.account.name,
                action="SCAN_RESULT"
            )
            logger.debug(f"Top: {top_info}", account=self.account.name)
        else:
            logger.info(
                f"📊 Scan: {len(markets)} mkts → {len(pre_candidates)} pre → 0 scored (none passed min_score={self.min_score})",
                account=self.account.name,
                action="NO_SCORED"
            )
        
        # Place orders on top candidates
        max_tick = self.preset.get('max_tick', 0.01)
        placed = 0
        
        for candidate in scored_candidates[:20]:  # Limit to top 20
            token_id = candidate['token_id']
            
            tick = self._get_tick_size(token_id)
            if tick > max_tick:
                self.placed_tokens.add(token_id)
                continue
            
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
                
                score = candidate['score']
                skew_ratio = candidate.get('skew_ratio', 0)
                is_dual_bet = candidate.get('is_binary_dual', False)
                
                # Enhanced logging with skew info
                skew_info = ""
                if skew_ratio > 1.5:
                    skew_info = f" | 🎯 Skew: {skew_ratio:.1f}x"
                if is_dual_bet:
                    skew_info += " | 🔀 DUAL-BET"
                
                logger.info(
                    f"Smart order: {candidate['question'][:30]} score={score.total_score:.0f} "
                    f"({', '.join(score.reasons[:3])}){skew_info}",
                    account=self.account.name,
                    action="SMART_ORDER"
                )
            else:
                self.placed_tokens.add(token_id)
            
            time.sleep(0.1)
        
        scan_duration = (time.time() - scan_start) * 1000
        
        logger.scan_complete(
            self.account.name,
            len(markets),
            len(scored_candidates),
            placed,
            scan_duration
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
        
        return placed, len(scored_candidates)
    
    def _get_tick_size(self, token_id: str) -> float:
        """Get tick size with caching"""
        if token_id in self.tick_cache:
            return self.tick_cache[token_id]
        
        tick = self.data_api.get_tick_size(token_id)
        self.tick_cache[token_id] = tick
        return tick
    
    def run(self):
        """Main smart sniper loop"""
        if not self.init():
            logger.error("Initialization failed", account=self.account.name)
            return
        
        scan_interval = self.preset.get('scan_interval', 0.7)
        no_candidates_pause = self.settings.no_candidates_pause_minutes * 60
        
        logger.info(
            f"Starting SMART sniper (interval: {scan_interval}s, min_score: {self.min_score})",
            account=self.account.name,
            action="SMART_SNIPER_START"
        )
        
        consecutive_empty = 0
        last_status_time = time.time()
        
        while self.running:
            try:
                self.cycle += 1
                
                placed, candidates = self.scan()
                
                # Periodic status every 2 minutes
                if time.time() - last_status_time > 120:
                    logger.info(
                        f"🔄 Status: cycle={self.cycle}, orders={self.orders_placed}, "
                        f"cached={len(self.placed_tokens)}, excluded={len(self.excluded_tokens)}",
                        account=self.account.name,
                        action="STATUS"
                    )
                    last_status_time = time.time()
                
                if candidates == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 10:
                        logger.info(
                            f"Pausing {no_candidates_pause//60} min - no quality candidates",
                            account=self.account.name,
                            action="SMART_PAUSE"
                        )
                        time.sleep(no_candidates_pause)
                        consecutive_empty = 0
                        self._load_existing_orders()
                        self._load_existing_positions()
                else:
                    consecutive_empty = 0
                
                time.sleep(scan_interval)
                
            except KeyboardInterrupt:
                logger.info(
                    f"Stopped. Total orders: {self.orders_placed}",
                    account=self.account.name,
                    action="SMART_SNIPER_STOP"
                )
                self.running = False
                break
                
            except Exception as e:
                logger.exception(f"Scan error: {e}", account=self.account.name)
                time.sleep(5)
