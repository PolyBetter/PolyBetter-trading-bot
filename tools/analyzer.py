#!/usr/bin/env python3
"""
Market Analyzer
===============
Analyze Polymarket data for insights:
- Market distribution
- Volume analysis
- Category breakdown
- Opportunity detection
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_presets
from core.data_api import DataAPI, extract_markets_from_events
from core.logger import get_logger

logger = get_logger("analyzer")


class MarketAnalyzer:
    """
    Analyze Polymarket markets and provide insights.
    """
    
    def __init__(self):
        self.data_api = DataAPI()
        self.presets_data = load_presets()
    
    def fetch_all_markets(self, progress_callback=None) -> Tuple[List[Dict], List[Dict]]:
        """
        Fetch all events and extract markets.
        
        Returns:
            (events, markets)
        """
        events = self.data_api.get_all_events(
            closed=False,
            progress_callback=progress_callback
        )
        markets = extract_markets_from_events(events)
        return events, markets
    
    def analyze_volume_distribution(self, markets: List[Dict]) -> Dict:
        """
        Analyze volume distribution.
        
        Returns stats about volume ranges.
        """
        ranges = [
            (0, 1000, "<$1k"),
            (1000, 10000, "$1k-$10k"),
            (10000, 50000, "$10k-$50k"),
            (50000, 100000, "$50k-$100k"),
            (100000, 500000, "$100k-$500k"),
            (500000, float('inf'), ">$500k"),
        ]
        
        result = {}
        total_volume = 0
        
        for min_v, max_v, label in ranges:
            matching = [m for m in markets 
                       if min_v <= float(m.get('volume', 0) or 0) < max_v]
            vol = sum(float(m.get('volume', 0) or 0) for m in matching)
            result[label] = {
                "count": len(matching),
                "percent": len(matching) / len(markets) * 100 if markets else 0,
                "volume": vol
            }
            total_volume += vol
        
        result["total"] = {
            "count": len(markets),
            "volume": total_volume
        }
        
        return result
    
    def analyze_categories(self, events: List[Dict]) -> Dict:
        """
        Analyze market categories from tags.
        """
        tags_count = defaultdict(int)
        
        for event in events:
            for tag in event.get('tags', []):
                label = tag.get('label', 'Unknown') if isinstance(tag, dict) else str(tag)
                tags_count[label] += 1
        
        # Sort by count
        sorted_tags = sorted(tags_count.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "total_tags": len(tags_count),
            "top_tags": sorted_tags[:20],
            "all_tags": dict(sorted_tags)
        }
    
    def analyze_preset_coverage(self, markets: List[Dict]) -> Dict:
        """
        Analyze how many markets each preset would cover.
        """
        presets = self.presets_data.get("presets", {})
        blocked_tags = self.presets_data.get("blocked_tags", {})
        blocked_keywords = self.presets_data.get("blocked_keywords", {})
        
        results = {}
        
        for preset_name, preset in presets.items():
            matching = 0
            blocked = {
                "sports": 0,
                "crypto": 0,
                "politics": 0,
                "volume": 0,
                "liquidity": 0
            }
            
            for market in markets:
                # Extract tags
                tags = []
                for t in market.get('tags', []) + market.get('event_tags', []):
                    if isinstance(t, dict):
                        tags.append((t.get('label', '') or '').lower())
                    else:
                        tags.append(str(t).lower())
                
                # Check blocks
                is_blocked = False
                
                # Sports
                if preset.get('block_sports', False):
                    for tag in tags:
                        if any(b in tag for b in blocked_tags.get('sports', [])):
                            blocked["sports"] += 1
                            is_blocked = True
                            break
                
                if is_blocked:
                    continue
                
                # Crypto
                if preset.get('block_crypto', False):
                    for tag in tags:
                        if any(b in tag for b in blocked_tags.get('crypto', [])):
                            blocked["crypto"] += 1
                            is_blocked = True
                            break
                
                if is_blocked:
                    continue
                
                # Politics
                if preset.get('block_politics', False):
                    text = f"{market.get('question', '')} {market.get('event_title', '')}".lower()
                    if any(kw in text for kw in blocked_keywords.get('politics', [])):
                        blocked["politics"] += 1
                        continue
                
                # Volume
                volume = float(market.get('volume', 0) or 0)
                if volume < preset.get('min_volume', 10000):
                    blocked["volume"] += 1
                    continue
                
                # Liquidity
                if preset.get('require_liquidity', False):
                    liquidity = float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0)
                    if liquidity < preset.get('min_liquidity', 0):
                        blocked["liquidity"] += 1
                        continue
                
                matching += 1
            
            results[preset_name] = {
                "name": preset.get('name', preset_name),
                "matching": matching,
                "percent": matching / len(markets) * 100 if markets else 0,
                "blocked": blocked
            }
        
        return results
    
    def find_opportunities(self, markets: List[Dict], 
                          min_volume: float = 10000,
                          max_tick: float = 0.01,
                          min_liquidity: float = 100) -> List[Dict]:
        """
        Find potential trading opportunities.
        
        Criteria:
        - Good volume
        - Reasonable tick size
        - Has liquidity
        - Not ending too soon
        """
        opportunities = []
        
        for market in markets:
            volume = float(market.get('volume', 0) or 0)
            if volume < min_volume:
                continue
            
            liquidity = float(market.get('liquidity', 0) or market.get('liquidityClob', 0) or 0)
            if liquidity < min_liquidity:
                continue
            
            # Parse prices
            prices = market.get('outcomePrices') or market.get('outcome_prices', '[]')
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    continue
            prices = [float(p) for p in prices] if prices else []
            
            # Look for low-priced outcomes
            for i, price in enumerate(prices):
                if 0.001 <= price <= 0.10:  # 0.1% to 10%
                    tokens = market.get('clobTokenIds') or market.get('clob_token_ids', [])
                    if isinstance(tokens, str):
                        try:
                            tokens = json.loads(tokens)
                        except:
                            continue
                    
                    outcomes = market.get('outcomes', [])
                    if isinstance(outcomes, str):
                        try:
                            outcomes = json.loads(outcomes)
                        except:
                            outcomes = []
                    
                    opportunities.append({
                        "question": market.get('question', '')[:60],
                        "outcome": outcomes[i] if i < len(outcomes) else f"#{i}",
                        "price": price,
                        "volume": volume,
                        "liquidity": liquidity,
                        "token_id": tokens[i] if i < len(tokens) else "",
                        "potential_return": f"{(1/price):.0f}x"
                    })
        
        # Sort by volume
        opportunities.sort(key=lambda x: x['volume'], reverse=True)
        
        return opportunities[:50]
    
    def generate_report(self) -> str:
        """
        Generate full analysis report.
        """
        print("Fetching markets...")
        events, markets = self.fetch_all_markets(
            progress_callback=lambda msg: print(f"  {msg}")
        )
        
        lines = [
            "=" * 60,
            "POLYMARKET MARKET ANALYSIS",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            ""
        ]
        
        # Overview
        lines.append("OVERVIEW")
        lines.append("-" * 40)
        lines.append(f"Total Events: {len(events):,}")
        lines.append(f"Total Markets: {len(markets):,}")
        
        total_volume = sum(float(m.get('volume', 0) or 0) for m in markets)
        total_liquidity = sum(float(m.get('liquidity', 0) or 0) for m in markets)
        lines.append(f"Total Volume: ${total_volume:,.0f}")
        lines.append(f"Total Liquidity: ${total_liquidity:,.0f}")
        lines.append("")
        
        # Volume distribution
        vol_dist = self.analyze_volume_distribution(markets)
        lines.append("VOLUME DISTRIBUTION")
        lines.append("-" * 40)
        for label, data in vol_dist.items():
            if label != "total":
                pct = data['percent']
                bar = "█" * int(pct / 3)
                lines.append(f"  {label:<15} {data['count']:>6,} ({pct:>5.1f}%) {bar}")
        lines.append("")
        
        # Categories
        categories = self.analyze_categories(events)
        lines.append("TOP CATEGORIES")
        lines.append("-" * 40)
        for tag, count in categories['top_tags'][:10]:
            pct = count / len(events) * 100
            lines.append(f"  {tag:<25} {count:>5} ({pct:>5.1f}%)")
        lines.append("")
        
        # Preset coverage
        coverage = self.analyze_preset_coverage(markets)
        lines.append("PRESET COVERAGE")
        lines.append("-" * 40)
        for name, data in coverage.items():
            lines.append(f"  {data['name']:<20} {data['matching']:>5} markets ({data['percent']:.1f}%)")
            if data['blocked']['sports'] > 0:
                lines.append(f"    - Sports blocked: {data['blocked']['sports']}")
            if data['blocked']['crypto'] > 0:
                lines.append(f"    - Crypto blocked: {data['blocked']['crypto']}")
            if data['blocked']['volume'] > 0:
                lines.append(f"    - Low volume: {data['blocked']['volume']}")
        lines.append("")
        
        # Opportunities
        opps = self.find_opportunities(markets)
        lines.append("TOP OPPORTUNITIES (Low price, high volume)")
        lines.append("-" * 40)
        for opp in opps[:10]:
            lines.append(f"  {opp['question']}")
            lines.append(f"    {opp['outcome']} @ ${opp['price']:.3f} ({opp['potential_return']})")
            lines.append(f"    Vol: ${opp['volume']/1000:.0f}k | Liq: ${opp['liquidity']:.0f}")
        
        return "\n".join(lines)


def main():
    """Run analyzer"""
    analyzer = MarketAnalyzer()
    report = analyzer.generate_report()
    print(report)
    
    # Save to file
    output_file = Path(__file__).parent.parent / "data" / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nReport saved to: {output_file}")


if __name__ == "__main__":
    main()
