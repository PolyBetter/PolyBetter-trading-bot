"""
Data API Client
===============
Async/sync client for Polymarket Data API with:
- Connection pooling
- Retry logic
- Rate limiting
- Caching
"""

import time
import asyncio
import httpx
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

from .config import GAMMA_API, CLOB_API, DATA_API
from .logger import get_logger

logger = get_logger()


@dataclass
class DataAPI:
    """
    Polymarket Data API client.
    Supports both sync and async operations.
    """
    
    proxy: str = ""
    timeout: float = 30.0
    max_retries: int = 3
    
    # Internal state
    _session: Optional[httpx.Client] = field(default=None, repr=False)
    _async_session: Optional[httpx.AsyncClient] = field(default=None, repr=False)
    
    def __post_init__(self):
        self._create_session()
    
    def _create_session(self):
        """Create HTTP session with proper settings"""
        proxy_url = None
        if self.proxy:
            proxy_url = self.proxy if self.proxy.startswith("http") else f"http://{self.proxy}"
        
        self._session = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            proxy=proxy_url,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json"
            }
        )
    
    async def _get_async_session(self) -> httpx.AsyncClient:
        """Get or create async session"""
        if self._async_session is None:
            proxy_url = None
            if self.proxy:
                proxy_url = self.proxy if self.proxy.startswith("http") else f"http://{self.proxy}"
            
            self._async_session = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                proxy=proxy_url,
                verify=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json"
                }
            )
        return self._async_session
    
    def close(self):
        """Close sessions"""
        if self._session:
            self._session.close()
        if self._async_session:
            asyncio.get_event_loop().run_until_complete(self._async_session.aclose())
    
    def _request(self, method: str, url: str, silent_404: bool = False, **kwargs) -> Optional[Dict]:
        """Make HTTP request with retry logic"""
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                start = time.time()
                response = self._session.request(method, url, **kwargs)
                duration = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    # Rate limited - wait and retry
                    wait = min(2 ** attempt, 30)
                    logger.warning(f"Rate limited, waiting {wait}s", action="RATE_LIMIT")
                    time.sleep(wait)
                elif response.status_code == 404 and silent_404:
                    # 404 for orderbook is normal (closed markets) - don't log
                    return None
                else:
                    logger.api_error(url, response.status_code, response.text[:200], duration)
                    
            except httpx.TimeoutException as e:
                last_error = f"Timeout: {e}"
                if attempt == self.max_retries - 1:  # Only log on final attempt
                    logger.warning(f"Request timeout after {self.max_retries} attempts", action="TIMEOUT")
                
            except Exception as e:
                last_error = str(e)
                if attempt == self.max_retries - 1:
                    logger.error(f"Request error: {e}", exc_info=True)
        
        return None
    
    async def _async_request(self, method: str, url: str, silent_404: bool = False, **kwargs) -> Optional[Dict]:
        """Make async HTTP request with retry logic"""
        session = await self._get_async_session()
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                start = time.time()
                response = await session.request(method, url, **kwargs)
                duration = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    wait = min(2 ** attempt, 30)
                    logger.warning(f"Rate limited, waiting {wait}s", action="RATE_LIMIT")
                    await asyncio.sleep(wait)
                elif response.status_code == 404 and silent_404:
                    # 404 for orderbook is normal (closed markets) - don't log
                    return None
                else:
                    logger.api_error(url, response.status_code, response.text[:200], duration)
                    
            except httpx.TimeoutException as e:
                last_error = f"Timeout: {e}"
                if attempt == self.max_retries - 1:
                    logger.warning(f"Async request timeout after {self.max_retries} attempts", action="TIMEOUT")
                
            except Exception as e:
                last_error = str(e)
                if attempt == self.max_retries - 1:
                    logger.error(f"Async request error: {e}", exc_info=True)
        
        return None
    
    # ==================== GAMMA API (Markets) ====================
    
    def get_tags(self, limit: int = 200) -> List[Dict]:
        """Get all available tags from Gamma API"""
        url = f"{GAMMA_API}/tags"
        params = {"limit": limit}
        result = self._request("GET", url, params=params)
        return result if result else []
    
    def get_sports(self) -> List[Dict]:
        """Get all sports leagues with their tag IDs"""
        url = f"{GAMMA_API}/sports"
        result = self._request("GET", url)
        return result if result else []
    
    def get_events(self, 
                   closed: bool = False, 
                   limit: int = 100, 
                   offset: int = 0,
                   exclude_tag_ids: List[int] = None,
                   end_date_min: str = None,
                   end_date_max: str = None) -> List[Dict]:
        """
        Get events from Gamma API.
        
        Args:
            closed: Filter for closed events
            limit: Max results per page
            offset: Pagination offset
            exclude_tag_ids: List of tag IDs to exclude (e.g., sports, esports)
            end_date_min: Min end date (ISO 8601), e.g. "2026-01-25T15:00:00Z"
            end_date_max: Max end date (ISO 8601)
        """
        url = f"{GAMMA_API}/events"
        params = {
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset
        }
        
        # Add exclude_tag_id (API accepts multiple as comma-separated or repeated params)
        if exclude_tag_ids:
            # Polymarket API accepts exclude_tag_id as array
            params["exclude_tag_id"] = ",".join(str(tid) for tid in exclude_tag_ids)
        
        if end_date_min:
            params["end_date_min"] = end_date_min
        if end_date_max:
            params["end_date_max"] = end_date_max
        
        result = self._request("GET", url, params=params)
        return result if result else []
    
    def get_all_events(self, 
                       closed: bool = False,
                       exclude_tag_ids: List[int] = None,
                       end_date_min: str = None,
                       end_date_max: str = None,
                       progress_callback: Callable[[str], None] = None) -> List[Dict]:
        """
        Fetch ALL events without limit.
        Uses pagination with large pages to get complete dataset.
        
        NOTE: Polymarket has 5000-7000+ open events. City weather markets
        appear at offset 4500+. We use limit=500 to reach them faster
        and allow more empty pages to handle API gaps.
        
        Args:
            closed: Filter for closed events
            exclude_tag_ids: List of tag IDs to exclude (sports, esports, etc.)
            end_date_min: Min end date (ISO 8601) - filter out events ending too soon
            end_date_max: Max end date (ISO 8601) - filter out events ending too late
            progress_callback: Optional callback for progress updates
        """
        all_events = []
        seen_ids = set()
        offset = 0
        page_size = 500
        consecutive_empty = 0
        max_consecutive_empty = 5  # Allow more gaps before stopping
        
        while True:
            events = self.get_events(
                closed=closed, 
                limit=page_size, 
                offset=offset,
                exclude_tag_ids=exclude_tag_ids,
                end_date_min=end_date_min,
                end_date_max=end_date_max
            )
            
            if not events:
                consecutive_empty += 1
                if consecutive_empty >= max_consecutive_empty:
                    break
                offset += page_size
                continue
            
            consecutive_empty = 0
            new_count = 0
            
            for event in events:
                event_id = event.get('id') or event.get('slug')
                if event_id and event_id not in seen_ids:
                    seen_ids.add(event_id)
                    all_events.append(event)
                    new_count += 1
            
            if progress_callback:
                progress_callback(f"Loaded {len(all_events)} events (offset: {offset})")
            
            if len(events) < page_size:
                break
            
            offset += page_size
            
            # Rate limit protection
            if offset % 2000 == 0:
                time.sleep(0.3)
        
        return all_events
    
    async def get_events_async(self, 
                               closed: bool = False, 
                               limit: int = 100, 
                               offset: int = 0,
                               exclude_tag_ids: List[int] = None,
                               end_date_min: str = None,
                               end_date_max: str = None) -> List[Dict]:
        """Async version of get_events with filtering support"""
        url = f"{GAMMA_API}/events"
        params = {
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset
        }
        
        if exclude_tag_ids:
            params["exclude_tag_id"] = ",".join(str(tid) for tid in exclude_tag_ids)
        if end_date_min:
            params["end_date_min"] = end_date_min
        if end_date_max:
            params["end_date_max"] = end_date_max
        
        result = await self._async_request("GET", url, params=params)
        return result if result else []
    
    async def get_all_events_async(self, 
                                   closed: bool = False,
                                   exclude_tag_ids: List[int] = None,
                                   end_date_min: str = None,
                                   end_date_max: str = None,
                                   max_concurrent: int = 5) -> List[Dict]:
        """
        Fetch ALL events using async parallel requests.
        Much faster than sync version.
        
        NOTE: Uses limit=500 per page to cover 5000-7000+ events
        (city weather markets appear at offset 4500+).
        """
        page_size = 500
        all_events = []
        seen_ids = set()
        
        # First, get initial batch to estimate total
        first_batch = await self.get_events_async(
            closed=closed, limit=page_size, offset=0,
            exclude_tag_ids=exclude_tag_ids,
            end_date_min=end_date_min,
            end_date_max=end_date_max
        )
        if not first_batch:
            return []
        
        for event in first_batch:
            event_id = event.get('id')
            if event_id:
                seen_ids.add(event_id)
                all_events.append(event)
        
        # If less than page_size, we're done
        if len(first_batch) < page_size:
            return all_events
        
        # Fetch remaining in parallel batches
        offset = page_size
        consecutive_empty_batches = 0
        while True:
            # Create batch of tasks
            tasks = []
            for i in range(max_concurrent):
                tasks.append(self.get_events_async(
                    closed=closed, limit=page_size, offset=offset + i * page_size,
                    exclude_tag_ids=exclude_tag_ids,
                    end_date_min=end_date_min,
                    end_date_max=end_date_max
                ))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            got_any = False
            for result in results:
                if isinstance(result, Exception):
                    continue
                if not result:
                    continue
                    
                got_any = True
                for event in result:
                    event_id = event.get('id')
                    if event_id and event_id not in seen_ids:
                        seen_ids.add(event_id)
                        all_events.append(event)
            
            if not got_any:
                consecutive_empty_batches += 1
                if consecutive_empty_batches >= 3:
                    break
            else:
                consecutive_empty_batches = 0
            
            offset += max_concurrent * page_size
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.1)
        
        return all_events
    
    # ==================== DATA API (Positions) ====================
    
    def get_positions(self, 
                      wallet: str, 
                      size_threshold: float = 0.1,
                      limit: int = 100,
                      offset: int = 0) -> List[Dict]:
        """Get positions for wallet"""
        url = f"{DATA_API}/positions"
        params = {
            "user": wallet,
            "sizeThreshold": size_threshold,
            "limit": limit,
            "offset": offset
        }
        result = self._request("GET", url, params=params)
        return result if result else []
    
    def get_all_positions(self, wallet: str, size_threshold: float = 0.01) -> List[Dict]:
        """Get ALL positions for wallet (handles pagination)"""
        all_positions = []
        offset = 0
        
        while True:
            positions = self.get_positions(
                wallet=wallet,
                size_threshold=size_threshold,
                limit=100,
                offset=offset
            )
            
            if not positions:
                break
            
            all_positions.extend(positions)
            
            if len(positions) < 100:
                break
            
            offset += 100
        
        return all_positions
    
    # ==================== CLOB API (Order Book) ====================
    
    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """Get order book for token (404 errors are silent - normal for closed markets)"""
        url = f"{CLOB_API}/book"
        result = self._request("GET", url, silent_404=True, params={"token_id": token_id})
        return result
    
    def get_tick_size(self, token_id: str) -> float:
        """Get tick size for token"""
        url = f"{CLOB_API}/tick-size"
        result = self._request("GET", url, params={"token_id": token_id})
        if result:
            return float(result.get("minimum_tick_size", 0.01))
        return 0.01
    
    async def get_orderbook_async(self, token_id: str) -> Optional[Dict]:
        """Async get order book (404 errors are silent - normal for closed markets)"""
        url = f"{CLOB_API}/book"
        return await self._async_request("GET", url, silent_404=True, params={"token_id": token_id})
    
    async def get_tick_sizes_async(self, token_ids: List[str]) -> Dict[str, float]:
        """Get tick sizes for multiple tokens in parallel"""
        async def get_one(token_id: str) -> tuple:
            url = f"{CLOB_API}/tick-size"
            result = await self._async_request("GET", url, params={"token_id": token_id})
            tick = float(result.get("minimum_tick_size", 0.01)) if result else 0.01
            return (token_id, tick)
        
        tasks = [get_one(tid) for tid in token_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        tick_sizes = {}
        for result in results:
            if isinstance(result, tuple):
                tick_sizes[result[0]] = result[1]
        
        return tick_sizes
    
    # ==================== MARKET ANALYSIS ====================
    
    def check_sell_liquidity(self, 
                            token_id: str, 
                            min_bid_size: float = 5.0,
                            min_bid_count: int = 1) -> tuple[bool, str]:
        """
        Check if market has enough buy-side liquidity.
        
        Returns:
            Tuple[bool, str]: (can_sell, reason)
        """
        book = self.get_orderbook(token_id)
        
        if not book:
            return False, "no_orderbook"
        
        bids = book.get('bids', [])
        
        if not bids:
            return False, "no_bids"
        
        if len(bids) < min_bid_count:
            return False, f"few_bids({len(bids)}<{min_bid_count})"
        
        total_bid_size = sum(float(b.get('size', 0)) for b in bids)
        if total_bid_size < min_bid_size:
            return False, f"small_bids({total_bid_size:.1f}<{min_bid_size})"
        
        return True, "ok"
    
    async def check_sell_liquidity_async(self,
                                         token_id: str,
                                         min_bid_size: float = 5.0,
                                         min_bid_count: int = 1) -> tuple[bool, str]:
        """Async version of check_sell_liquidity"""
        book = await self.get_orderbook_async(token_id)
        
        if not book:
            return False, "no_orderbook"
        
        bids = book.get('bids', [])
        
        if not bids:
            return False, "no_bids"
        
        if len(bids) < min_bid_count:
            return False, f"few_bids({len(bids)}<{min_bid_count})"
        
        total_bid_size = sum(float(b.get('size', 0)) for b in bids)
        if total_bid_size < min_bid_size:
            return False, f"small_bids({total_bid_size:.1f}<{min_bid_size})"
        
        return True, "ok"
    
    def get_market_spread(self, token_id: str) -> Optional[Dict]:
        """
        Get market spread analysis.
        
        Returns dict with:
        - best_bid, best_ask, spread, spread_pct
        - bid_depth, ask_depth
        - mid_price
        """
        book = self.get_orderbook(token_id)
        if not book:
            return None
        
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        
        if not bids and not asks:
            return None
        
        best_bid = float(bids[0].get('price', 0)) if bids else 0
        best_ask = float(asks[0].get('price', 1)) if asks else 1
        
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.5
        spread_pct = spread / mid_price if mid_price > 0 else 1
        
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "mid_price": mid_price,
            "bid_depth": sum(float(b.get('size', 0)) for b in bids),
            "ask_depth": sum(float(a.get('size', 0)) for a in asks),
            "bid_count": len(bids),
            "ask_count": len(asks)
        }


# ==================== HELPER FUNCTIONS ====================

def extract_markets_from_events(events: List[Dict]) -> List[Dict]:
    """Extract all markets from events list with event metadata"""
    markets = []
    seen = set()
    
    for event in events:
        event_tags = event.get('tags', [])
        event_title = event.get('title', '')
        event_id = event.get('id', '')
        
        for market in event.get('markets', []):
            market_id = market.get('id') or market.get('conditionId')
            if market_id and market_id not in seen:
                seen.add(market_id)
                market['event_tags'] = event_tags
                market['event_title'] = event_title
                market['event_id'] = event_id
                markets.append(market)
    
    return markets
