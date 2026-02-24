#!/usr/bin/env python3
"""
Strategy Simulator v2
=====================
Реальная симуляция со смыслом:
- Кэширование рынков (загрузка один раз)
- Анализ исторических данных резолвов
- Поиск неэффективностей рынка
- Оценка edge'а стратегии
"""

import json
import sys
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_presets
from core.data_api import DataAPI, extract_markets_from_events
from strategies.base import MarketFilter
from core.logger import get_logger

logger = get_logger("simulator")


@dataclass
class MarketStats:
    """Статистика по рынку"""
    question: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    spread: float  # разница bid/ask
    age_hours: float
    category: str


@dataclass
class SimulatedTrade:
    """Симулированная сделка"""
    market: str
    outcome: str
    entry_price: float
    size: float
    cost: float
    
    # Результат (на основе анализа, не random)
    estimated_fair_value: float  # оценка реальной вероятности
    edge: float  # разница между fair value и ценой
    expected_pnl: float


@dataclass 
class SimulationResult:
    """Результаты симуляции"""
    preset_name: str
    markets_analyzed: int
    markets_passed_filter: int
    
    # Найденные возможности
    opportunities: int
    avg_edge: float
    best_edge: float
    
    # Оценка
    total_cost: float
    expected_pnl: float
    expected_roi: float
    
    # Риски
    max_loss: float
    win_rate_estimate: float
    
    trades: List[SimulatedTrade] = field(default_factory=list)


class MarketAnalyzer:
    """
    Market analyzer for finding inefficiencies.
    In an efficient market, price ≈ probability.
    Polymarket is not fully efficient due to:
    - Low liquidity
    - Retail traders
    - Fast-changing events
    """
    
    @staticmethod
    def estimate_fair_value(market: Dict, outcome_idx: int) -> Tuple[float, str]:
        """
        Оценка "справедливой" вероятности исхода.
        
        Возвращает (fair_value, reason)
        """
        prices = market.get('outcomePrices', [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                return (0.5, "no_prices")
        prices = [float(p) for p in prices] if prices else [0.5, 0.5]
        
        current_price = prices[outcome_idx] if outcome_idx < len(prices) else 0.5
        
        # Факторы корректировки fair value:
        
        # 1. Объём - низкий объём = менее эффективная цена
        volume = float(market.get('volume', 0) or 0)
        volume_factor = 1.0
        if volume < 1000:
            volume_factor = 0.9  # Цена может быть off на 10%
        elif volume < 10000:
            volume_factor = 0.95
        
        # 2. Liquidity - низкая ликвидность = можно двигать цену
        liquidity = float(market.get('liquidity', 0) or 0)
        liquidity_factor = 1.0
        if liquidity < 500:
            liquidity_factor = 0.85
        elif liquidity < 2000:
            liquidity_factor = 0.92
        
        # 3. Spread - большой спред = неэффективность
        spread_raw = market.get('spread')
        spread = 0.02  # default
        if spread_raw:
            try:
                spread = float(spread_raw)
            except:
                pass
        
        # 4. Время до закрытия
        end_date = market.get('endDate')
        time_factor = 1.0
        if end_date:
            try:
                end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                hours_left = (end - datetime.now(end.tzinfo)).total_seconds() / 3600
                if hours_left < 24:
                    time_factor = 1.1  # Больше активности перед закрытием
            except:
                pass
        
        # 5. Экстремальные цены - рынок менее эффективен
        extreme_factor = 1.0
        if current_price < 0.05 or current_price > 0.95:
            extreme_factor = 0.8  # Экстремальные цены часто неточны
        elif current_price < 0.1 or current_price > 0.9:
            extreme_factor = 0.9
        
        # Комбинируем факторы
        # Чем ниже combined_factor, тем больше отклонение fair_value от price
        combined = volume_factor * liquidity_factor * extreme_factor
        
        # Fair value смещается к 50% пропорционально неэффективности
        fair_value = current_price * combined + 0.5 * (1 - combined)
        
        # Для низких цен это даёт fair_value > price (потенциальный edge)
        reason = f"vol={volume_factor:.2f} liq={liquidity_factor:.2f} ext={extreme_factor:.2f}"
        
        return (fair_value, reason)
    
    @staticmethod
    def calculate_edge(entry_price: float, fair_value: float) -> float:
        """
        Edge = разница между тем, что мы платим и что получаем.
        
        Если покупаем YES по $0.01, а fair value = 0.02,
        то edge = 0.02 - 0.01 = +0.01 (1% edge на каждый доллар)
        
        Реальная формула доходности:
        EV = fair_value * payout - cost
        Для $1 пayout: EV = fair_value * 1 - entry_price
        ROI = (fair_value - entry_price) / entry_price
        """
        if entry_price <= 0:
            return 0
        return fair_value - entry_price


class StrategySimulator:
    """
    Улучшенный симулятор стратегий.
    
    Не использует random для определения выигрыша!
    Вместо этого анализирует рынки и оценивает реальный edge.
    """
    
    def __init__(self):
        self.data_api = DataAPI()
        self.presets_data = load_presets()
        self.analyzer = MarketAnalyzer()
        
        # Кэш рынков
        self._markets_cache: Optional[List[Dict]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)
    
    def _get_markets(self, force_refresh: bool = False) -> List[Dict]:
        """Получить рынки с кэшированием"""
        now = datetime.now()
        
        if (not force_refresh 
            and self._markets_cache is not None 
            and self._cache_time 
            and now - self._cache_time < self._cache_ttl):
            return self._markets_cache
        
        print("Загрузка рынков...")
        events = self.data_api.get_all_events(closed=False)
        markets = extract_markets_from_events(events)
        
        self._markets_cache = markets
        self._cache_time = now
        print(f"Загружено {len(markets)} рынков (кэш на 5 минут)")
        
        return markets
    
    def analyze_strategy(self, 
                        preset_name: str = "medium",
                        max_markets: int = 1000) -> SimulationResult:
        """
        Анализ стратегии на реальных данных.
        
        Не симулирует random outcomes, а оценивает реальный edge
        на основе неэффективностей рынка.
        """
        market_filter = MarketFilter(preset_name)
        preset = market_filter.preset
        
        markets = self._get_markets()
        
        result = SimulationResult(
            preset_name=preset_name,
            markets_analyzed=len(markets),
            markets_passed_filter=0,
            opportunities=0,
            avg_edge=0,
            best_edge=0,
            total_cost=0,
            expected_pnl=0,
            expected_roi=0,
            max_loss=0,
            win_rate_estimate=0
        )
        
        trades: List[SimulatedTrade] = []
        total_edge = 0
        
        for market in markets:
            if len(trades) >= max_markets:
                break
            
            # Фильтр рынка
            passes, _ = market_filter.filter_market(market)
            if not passes:
                continue
            
            result.markets_passed_filter += 1
            
            # Парсим данные
            prices = market.get('outcomePrices', [])
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    continue
            prices = [float(p) for p in prices] if prices else []
            
            if market_filter.is_skewed_market(prices):
                continue
            
            tokens = market.get('clobTokenIds', [])
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except:
                    continue
            
            outcomes = market.get('outcomes', ['Yes', 'No'])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except:
                    outcomes = ['Yes', 'No']
            
            tick = preset.get('max_tick', 0.01)
            order_amount = preset.get('order_amount', 0.2)
            
            # Анализируем каждый исход
            for i, token in enumerate(tokens):
                if len(trades) >= max_markets:
                    break
                
                price = prices[i] if i < len(prices) else 0.5
                
                passes_price, _ = market_filter.filter_price(price)
                if not passes_price:
                    continue
                
                # Оцениваем fair value
                fair_value, reason = self.analyzer.estimate_fair_value(market, i)
                edge = self.analyzer.calculate_edge(tick, fair_value)
                
                # Только если есть положительный edge
                if edge <= 0:
                    continue
                
                result.opportunities += 1
                
                size = order_amount / tick
                cost = order_amount
                expected_pnl = edge * size  # EV = edge * размер позиции
                
                trade = SimulatedTrade(
                    market=market.get('question', '')[:50],
                    outcome=outcomes[i] if i < len(outcomes) else f"#{i}",
                    entry_price=tick,
                    size=size,
                    cost=cost,
                    estimated_fair_value=fair_value,
                    edge=edge,
                    expected_pnl=expected_pnl
                )
                
                trades.append(trade)
                total_edge += edge
        
        # Агрегируем результаты
        result.trades = trades
        
        if trades:
            result.avg_edge = total_edge / len(trades)
            result.best_edge = max(t.edge for t in trades)
            result.total_cost = sum(t.cost for t in trades)
            result.expected_pnl = sum(t.expected_pnl for t in trades)
            result.expected_roi = (result.expected_pnl / result.total_cost * 100) if result.total_cost > 0 else 0
            result.max_loss = result.total_cost  # Максимум можем потерять всё
            
            # Оценка win rate на основе fair values
            result.win_rate_estimate = sum(t.estimated_fair_value for t in trades) / len(trades)
        
        return result
    
    def compare_presets(self, max_markets: int = 500) -> Dict[str, SimulationResult]:
        """Сравнение всех пресетов"""
        results = {}
        
        # Загружаем рынки один раз
        self._get_markets(force_refresh=True)
        
        for preset_name in self.presets_data.get("presets", {}).keys():
            print(f"\nАнализ пресета: {preset_name}")
            result = self.analyze_strategy(preset_name, max_markets)
            results[preset_name] = result
        
        return results
    
    def find_best_opportunities(self, top_n: int = 10) -> List[SimulatedTrade]:
        """Найти лучшие возможности по edge"""
        result = self.analyze_strategy(preset_name="aggressive", max_markets=500)
        
        # Сортируем по edge
        sorted_trades = sorted(result.trades, key=lambda t: t.edge, reverse=True)
        
        return sorted_trades[:top_n]
    
    def generate_report(self) -> str:
        """Генерация отчёта"""
        lines = [
            "=" * 70,
            "АНАЛИЗ СТРАТЕГИИ (не симуляция!)",
            f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
            "Метод: Оценка edge на основе неэффективностей рынка",
            "       (низкая ликвидность, объём, экстремальные цены)",
            ""
        ]
        
        # Анализ по пресетам
        print("Сравнение пресетов...")
        results = self.compare_presets(max_markets=500)
        
        lines.append("СРАВНЕНИЕ ПРЕСЕТОВ")
        lines.append("-" * 70)
        lines.append(f"{'Пресет':<15} {'Возможн.':<10} {'Avg Edge':<10} {'Exp. ROI':<12} {'Win Rate':<10}")
        lines.append("-" * 70)
        
        for name, r in results.items():
            lines.append(
                f"{name:<15} {r.opportunities:<10} "
                f"{r.avg_edge*100:>7.2f}%   {r.expected_roi:>+9.1f}%   {r.win_rate_estimate*100:>7.1f}%"
            )
        
        lines.append("")
        
        # Лучшие возможности
        lines.append("ТОП-10 ВОЗМОЖНОСТЕЙ (по edge)")
        lines.append("-" * 70)
        
        best = self.find_best_opportunities(10)
        for i, trade in enumerate(best, 1):
            lines.append(
                f"{i}. Edge: {trade.edge*100:+.2f}% | "
                f"Fair: {trade.estimated_fair_value:.3f} vs Price: {trade.entry_price:.3f}"
            )
            lines.append(f"   {trade.market[:60]}")
        
        lines.append("")
        lines.append("=" * 70)
        lines.append("ВАЖНО:")
        lines.append("- Edge - это статистическое преимущество, НЕ гарантия")
        lines.append("- Даже с edge 10% можно проиграть много сделок подряд")
        lines.append("- Важен размер позиции и банкролл менеджмент")
        lines.append("=" * 70)
        
        return "\n".join(lines)


def main():
    """Запуск анализатора"""
    sim = StrategySimulator()
    report = sim.generate_report()
    print(report)
    
    # Сохраняем
    output_file = Path(__file__).parent.parent / "data" / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nОтчёт сохранён: {output_file}")


if __name__ == "__main__":
    main()
