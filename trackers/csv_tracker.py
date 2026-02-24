"""
CSV Data Trackers
=================
Structured CSV logging for:
- Trade history (every order placed/filled/cancelled)
- Position snapshots (periodic state of all positions)
- P&L tracking (running profit/loss calculations)
"""

import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from threading import Lock

BASE_DIR = Path(__file__).parent.parent


@dataclass
class TradeRecord:
    """Single trade record"""
    timestamp: str
    account: str
    action: str  # ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED, ORDER_FAILED
    token_id: str
    market_title: str
    outcome: str
    side: str  # BUY, SELL
    price: float
    size: float
    value: float  # price * size
    order_id: str
    order_type: str  # GTC, GTD, FOK, FAK
    status: str
    error: str = ""
    duration_ms: float = 0
    
    def to_row(self) -> list:
        return [
            self.timestamp, self.account, self.action, self.token_id[:20],
            self.market_title[:50], self.outcome, self.side, 
            f"{self.price:.4f}", f"{self.size:.2f}", f"{self.value:.2f}",
            self.order_id[:20] if self.order_id else "",
            self.order_type, self.status, self.error[:100],
            f"{self.duration_ms:.1f}" if self.duration_ms else ""
        ]


@dataclass
class PositionSnapshot:
    """Position state at a point in time"""
    timestamp: str
    account: str
    token_id: str
    market_title: str
    outcome: str
    size: float
    avg_price: float
    current_price: float
    current_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    def to_row(self) -> list:
        return [
            self.timestamp, self.account, self.token_id[:20],
            self.market_title[:50], self.outcome,
            f"{self.size:.2f}", f"{self.avg_price:.4f}", f"{self.current_price:.4f}",
            f"{self.current_value:.2f}", f"{self.cost_basis:.2f}",
            f"{self.unrealized_pnl:+.2f}", f"{self.unrealized_pnl_pct:+.1f}"
        ]


@dataclass
class PnLRecord:
    """P&L record for tracking profitability"""
    timestamp: str
    account: str
    total_positions: int
    total_orders: int
    total_value: float
    total_cost: float
    unrealized_pnl: float
    realized_pnl: float  # From closed positions
    usdc_balance: float
    total_equity: float  # usdc + positions value
    
    def to_row(self) -> list:
        return [
            self.timestamp, self.account,
            str(self.total_positions), str(self.total_orders),
            f"{self.total_value:.2f}", f"{self.total_cost:.2f}",
            f"{self.unrealized_pnl:+.2f}", f"{self.realized_pnl:+.2f}",
            f"{self.usdc_balance:.2f}", f"{self.total_equity:.2f}"
        ]


class TradeTracker:
    """
    Tracks all trade activity.
    
    Records:
    - Order placements (BUY/SELL)
    - Order fills
    - Order cancellations
    - Order failures with full error details
    """
    
    HEADERS = [
        "timestamp", "account", "action", "token_id", "market_title",
        "outcome", "side", "price", "size", "value", "order_id",
        "order_type", "status", "error", "duration_ms"
    ]
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or BASE_DIR / "data"
        self.data_dir.mkdir(exist_ok=True)
        self._lock = Lock()
        self._init_file()
    
    def _get_file_path(self) -> Path:
        """Get current file path (daily rotation)"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self.data_dir / f"trades_{date_str}.csv"
    
    def _init_file(self):
        """Initialize CSV file with headers if needed"""
        file_path = self._get_file_path()
        if not file_path.exists():
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)
    
    def record(self, 
               account: str,
               action: str,
               token_id: str,
               side: str,
               price: float,
               size: float,
               order_id: str = "",
               order_type: str = "GTC",
               status: str = "PENDING",
               market_title: str = "",
               outcome: str = "",
               error: str = "",
               duration_ms: float = 0):
        """Record a trade event"""
        
        record = TradeRecord(
            timestamp=datetime.now().isoformat(),
            account=account,
            action=action,
            token_id=token_id,
            market_title=market_title,
            outcome=outcome,
            side=side,
            price=price,
            size=size,
            value=price * size,
            order_id=order_id,
            order_type=order_type,
            status=status,
            error=error,
            duration_ms=duration_ms
        )
        
        with self._lock:
            self._init_file()  # Ensure file exists (daily rotation)
            with open(self._get_file_path(), 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(record.to_row())
    
    def order_placed(self, account: str, token_id: str, side: str, 
                    price: float, size: float, order_id: str,
                    order_type: str = "GTC", market_title: str = "",
                    outcome: str = "", duration_ms: float = 0):
        """Record order placement"""
        self.record(
            account=account,
            action="ORDER_PLACED",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_id=order_id,
            order_type=order_type,
            status="LIVE",
            market_title=market_title,
            outcome=outcome,
            duration_ms=duration_ms
        )
    
    def order_filled(self, account: str, token_id: str, side: str,
                    price: float, size: float, order_id: str,
                    market_title: str = "", outcome: str = ""):
        """Record order fill"""
        self.record(
            account=account,
            action="ORDER_FILLED",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_id=order_id,
            status="FILLED",
            market_title=market_title,
            outcome=outcome
        )
    
    def order_failed(self, account: str, token_id: str, side: str,
                    price: float, size: float, error: str,
                    order_type: str = "GTC", duration_ms: float = 0):
        """Record order failure with full error"""
        self.record(
            account=account,
            action="ORDER_FAILED",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_type=order_type,
            status="FAILED",
            error=error,  # Full error, not truncated!
            duration_ms=duration_ms
        )
    
    def order_cancelled(self, account: str, token_id: str, order_id: str):
        """Record order cancellation"""
        self.record(
            account=account,
            action="ORDER_CANCELLED",
            token_id=token_id,
            side="",
            price=0,
            size=0,
            order_id=order_id,
            status="CANCELLED"
        )
    
    def get_stats(self, account: str = None, days: int = 7) -> Dict:
        """Get trade statistics"""
        stats = {
            "total_orders": 0,
            "placed": 0,
            "filled": 0,
            "failed": 0,
            "cancelled": 0,
            "total_volume": 0,
            "by_side": {"BUY": 0, "SELL": 0},
            "errors": {}
        }
        
        # Read recent files
        for i in range(days):
            date = datetime.now() - __import__('datetime').timedelta(days=i)
            file_path = self.data_dir / f"trades_{date.strftime('%Y-%m-%d')}.csv"
            
            if not file_path.exists():
                continue
            
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if account and row.get('account') != account:
                        continue
                    
                    stats["total_orders"] += 1
                    action = row.get('action', '')
                    
                    if action == "ORDER_PLACED":
                        stats["placed"] += 1
                    elif action == "ORDER_FILLED":
                        stats["filled"] += 1
                    elif action == "ORDER_FAILED":
                        stats["failed"] += 1
                        error = row.get('error', 'unknown')[:50]
                        stats["errors"][error] = stats["errors"].get(error, 0) + 1
                    elif action == "ORDER_CANCELLED":
                        stats["cancelled"] += 1
                    
                    side = row.get('side', '')
                    if side in stats["by_side"]:
                        stats["by_side"][side] += 1
                    
                    try:
                        stats["total_volume"] += float(row.get('value', 0))
                    except:
                        pass
        
        return stats


class PositionTracker:
    """
    Tracks position snapshots over time.
    
    Useful for:
    - Historical position analysis
    - P&L attribution
    - Position sizing analysis
    """
    
    HEADERS = [
        "timestamp", "account", "token_id", "market_title", "outcome",
        "size", "avg_price", "current_price", "current_value",
        "cost_basis", "unrealized_pnl", "unrealized_pnl_pct"
    ]
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or BASE_DIR / "data"
        self.data_dir.mkdir(exist_ok=True)
        self._lock = Lock()
    
    def _get_file_path(self) -> Path:
        """Get current file path (daily rotation)"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self.data_dir / f"positions_{date_str}.csv"
    
    def _init_file(self):
        """Initialize CSV file with headers if needed"""
        file_path = self._get_file_path()
        if not file_path.exists():
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)
    
    def snapshot(self, account: str, positions: List[Dict]):
        """
        Record snapshot of all positions for an account.
        
        Args:
            account: Account name
            positions: List of position dicts from Data API
        """
        timestamp = datetime.now().isoformat()
        
        with self._lock:
            self._init_file()
            
            with open(self._get_file_path(), 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                for pos in positions:
                    size = float(pos.get('size', 0) or 0)
                    avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
                    current_price = float(pos.get('curPrice', 0) or 0)
                    
                    current_value = size * current_price
                    cost_basis = size * avg_price
                    pnl = current_value - cost_basis
                    pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
                    
                    snapshot = PositionSnapshot(
                        timestamp=timestamp,
                        account=account,
                        token_id=pos.get('asset', '') or pos.get('tokenId', ''),
                        market_title=pos.get('title', '') or '',
                        outcome=pos.get('outcome', '') or '',
                        size=size,
                        avg_price=avg_price,
                        current_price=current_price,
                        current_value=current_value,
                        cost_basis=cost_basis,
                        unrealized_pnl=pnl,
                        unrealized_pnl_pct=pnl_pct
                    )
                    
                    writer.writerow(snapshot.to_row())


class PnLTracker:
    """
    Tracks account P&L over time.
    
    Records:
    - Total position value
    - Unrealized P&L
    - Realized P&L (from closes)
    - USDC balance
    - Total equity
    """
    
    HEADERS = [
        "timestamp", "account", "total_positions", "total_orders",
        "total_value", "total_cost", "unrealized_pnl", "realized_pnl",
        "usdc_balance", "total_equity"
    ]
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or BASE_DIR / "data"
        self.data_dir.mkdir(exist_ok=True)
        self._lock = Lock()
        self._realized_pnl: Dict[str, float] = {}  # Track realized P&L per account
    
    def _get_file_path(self) -> Path:
        """Get file path (monthly rotation for P&L)"""
        date_str = datetime.now().strftime("%Y-%m")
        return self.data_dir / f"pnl_{date_str}.csv"
    
    def _init_file(self):
        """Initialize CSV file with headers if needed"""
        file_path = self._get_file_path()
        if not file_path.exists():
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)
    
    def add_realized_pnl(self, account: str, pnl: float):
        """Add realized P&L from closed position"""
        if account not in self._realized_pnl:
            self._realized_pnl[account] = 0
        self._realized_pnl[account] += pnl
    
    def record(self,
               account: str,
               positions: List[Dict],
               orders: List[Dict],
               usdc_balance: float):
        """
        Record P&L snapshot.
        
        Args:
            account: Account name
            positions: List of position dicts
            orders: List of order dicts
            usdc_balance: Current USDC balance
        """
        timestamp = datetime.now().isoformat()
        
        # Calculate totals
        total_value = 0
        total_cost = 0
        
        for pos in positions:
            size = float(pos.get('size', 0) or 0)
            avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
            current_price = float(pos.get('curPrice', 0) or 0)
            
            total_value += size * current_price
            total_cost += size * avg_price
        
        unrealized_pnl = total_value - total_cost
        realized_pnl = self._realized_pnl.get(account, 0)
        total_equity = usdc_balance + total_value
        
        record = PnLRecord(
            timestamp=timestamp,
            account=account,
            total_positions=len(positions),
            total_orders=len(orders),
            total_value=total_value,
            total_cost=total_cost,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            usdc_balance=usdc_balance,
            total_equity=total_equity
        )
        
        with self._lock:
            self._init_file()
            with open(self._get_file_path(), 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(record.to_row())
    
    def get_summary(self, account: str = None) -> Dict:
        """Get P&L summary from recent records"""
        summary = {
            "first_record": None,
            "last_record": None,
            "starting_equity": 0,
            "current_equity": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "records_count": 0
        }
        
        file_path = self._get_file_path()
        if not file_path.exists():
            return summary
        
        records = []
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if account and row.get('account') != account:
                    continue
                records.append(row)
        
        if not records:
            return summary
        
        summary["first_record"] = records[0].get('timestamp')
        summary["last_record"] = records[-1].get('timestamp')
        summary["starting_equity"] = float(records[0].get('total_equity', 0))
        summary["current_equity"] = float(records[-1].get('total_equity', 0))
        summary["total_pnl"] = summary["current_equity"] - summary["starting_equity"]
        summary["total_pnl_pct"] = (summary["total_pnl"] / summary["starting_equity"] * 100) if summary["starting_equity"] > 0 else 0
        summary["records_count"] = len(records)
        
        return summary


# ==================== GLOBAL INSTANCES ====================

_trade_tracker: Optional[TradeTracker] = None
_position_tracker: Optional[PositionTracker] = None
_pnl_tracker: Optional[PnLTracker] = None


def get_trade_tracker() -> TradeTracker:
    global _trade_tracker
    if _trade_tracker is None:
        _trade_tracker = TradeTracker()
    return _trade_tracker


def get_position_tracker() -> PositionTracker:
    global _position_tracker
    if _position_tracker is None:
        _position_tracker = PositionTracker()
    return _position_tracker


def get_pnl_tracker() -> PnLTracker:
    global _pnl_tracker
    if _pnl_tracker is None:
        _pnl_tracker = PnLTracker()
    return _pnl_tracker
