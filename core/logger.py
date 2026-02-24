"""
Advanced Logging System
=======================
Structured logging with:
- Full debug logs to file (nothing truncated)
- Clean user-facing output
- JSON-structured logs for parsing
- Automatic rotation
"""

import logging
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, Dict
from logging.handlers import RotatingFileHandler
import traceback


BASE_DIR = Path(__file__).parent.parent


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logs"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields
        if hasattr(record, 'account'):
            log_data['account'] = record.account
        if hasattr(record, 'action'):
            log_data['action'] = record.action
        if hasattr(record, 'details'):
            log_data['details'] = record.details
        if hasattr(record, 'result'):
            log_data['result'] = record.result
        if hasattr(record, 'error'):
            log_data['error'] = record.error
        if hasattr(record, 'duration_ms'):
            log_data['duration_ms'] = record.duration_ms
            
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': ''.join(traceback.format_exception(*record.exc_info))
            }
        
        return json.dumps(log_data, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Clean console formatter with colors"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        # Time format
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        
        # Color based on level
        color = self.COLORS.get(record.levelname, '')
        
        # Account prefix if present
        account = f"[{record.account}] " if hasattr(record, 'account') else ""
        
        # Format message
        msg = record.getMessage()
        
        # Add action/result for structured logs
        if hasattr(record, 'action'):
            msg = f"[{record.action}] {msg}"
        if hasattr(record, 'result'):
            msg = f"{msg} → {record.result}"
        
        return f"{color}[{timestamp}] {account}{msg}{self.RESET}"


class FullFileFormatter(logging.Formatter):
    """Full detailed formatter for file logs (nothing truncated)"""
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Build detailed log line
        parts = [
            f"[{timestamp}]",
            f"[{record.levelname:8}]",
            f"[{record.name}]",
            f"[{record.module}.{record.funcName}:{record.lineno}]"
        ]
        
        if hasattr(record, 'account'):
            parts.append(f"[ACC:{record.account}]")
        if hasattr(record, 'action'):
            parts.append(f"[ACT:{record.action}]")
        
        parts.append(record.getMessage())
        
        if hasattr(record, 'details'):
            parts.append(f"| details={record.details}")
        if hasattr(record, 'result'):
            parts.append(f"| result={record.result}")
        if hasattr(record, 'error'):
            parts.append(f"| error={record.error}")
        if hasattr(record, 'duration_ms'):
            parts.append(f"| duration={record.duration_ms}ms")
        
        line = " ".join(parts)
        
        # Add full exception traceback
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info))
        
        return line


class Logger:
    """Main logger class with multiple outputs"""
    
    def __init__(self, 
                 name: str = "polymarket",
                 log_dir: Optional[Path] = None,
                 console_level: int = logging.INFO,
                 file_level: int = logging.DEBUG,
                 max_size_mb: int = 50):
        
        self.name = name
        self.log_dir = log_dir or BASE_DIR / "logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # Create main logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        
        # Console handler (clean output)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(ConsoleFormatter())
        self.logger.addHandler(console_handler)
        
        # Full file handler (detailed, nothing truncated)
        log_file = self.log_dir / f"{name}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(FullFileFormatter())
        self.logger.addHandler(file_handler)
        
        # JSON structured log file (for parsing/analysis)
        json_file = self.log_dir / f"{name}.json.log"
        json_handler = RotatingFileHandler(
            json_file,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=3,
            encoding='utf-8'
        )
        json_handler.setLevel(logging.DEBUG)
        json_handler.setFormatter(JSONFormatter())
        self.logger.addHandler(json_handler)
        
        # Error-only file (for quick debugging)
        error_file = self.log_dir / f"{name}_errors.log"
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=3,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(FullFileFormatter())
        self.logger.addHandler(error_handler)
    
    def _log(self, level: int, msg: str, **kwargs):
        """Internal log method with extra fields"""
        extra = {k: v for k, v in kwargs.items() if v is not None}
        self.logger.log(level, msg, extra=extra)
    
    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)
    
    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)
    
    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)
    
    def error(self, msg: str, exc_info: bool = False, **kwargs):
        self.logger.error(msg, exc_info=exc_info, extra=kwargs)
    
    def critical(self, msg: str, exc_info: bool = False, **kwargs):
        self.logger.critical(msg, exc_info=exc_info, extra=kwargs)
    
    def exception(self, msg: str, **kwargs):
        """Log exception with full traceback"""
        self.logger.exception(msg, extra=kwargs)
    
    # Structured logging methods
    def order_placed(self, account: str, token_id: str, side: str, price: float, 
                    size: float, order_id: str = None, duration_ms: float = None):
        self.info(
            f"Order placed: {side} {size:.2f} @ ${price:.4f}",
            account=account,
            action="ORDER_PLACED",
            details={"token_id": token_id[:20], "side": side, "price": price, "size": size},
            result=order_id,
            duration_ms=duration_ms
        )
    
    def order_failed(self, account: str, token_id: str, error: str, duration_ms: float = None):
        self.warning(
            f"Order failed: {error}",
            account=account,
            action="ORDER_FAILED",
            details={"token_id": token_id[:20]},
            error=error,
            duration_ms=duration_ms
        )
    
    def position_closed(self, account: str, token_id: str, size: float, 
                       price: float, pnl: float, duration_ms: float = None):
        self.info(
            f"Position closed: {size:.2f} @ ${price:.4f} | PnL: ${pnl:+.2f}",
            account=account,
            action="POSITION_CLOSED",
            details={"token_id": token_id[:20], "size": size, "price": price},
            result=f"PnL: ${pnl:+.2f}",
            duration_ms=duration_ms
        )
    
    def scan_complete(self, account: str, markets_scanned: int, candidates: int, 
                     orders_placed: int, duration_ms: float):
        self.info(
            f"Scan: {markets_scanned} markets → {candidates} candidates → {orders_placed} orders",
            account=account,
            action="SCAN_COMPLETE",
            details={"markets": markets_scanned, "candidates": candidates, "orders": orders_placed},
            duration_ms=duration_ms
        )
    
    def api_error(self, endpoint: str, status_code: int, error: str, duration_ms: float = None):
        self.error(
            f"API Error: {endpoint} returned {status_code}",
            action="API_ERROR",
            details={"endpoint": endpoint, "status_code": status_code},
            error=error,
            duration_ms=duration_ms
        )
    
    def proxy_status(self, account: str, proxy: str, ip: str, success: bool):
        level = logging.INFO if success else logging.WARNING
        self._log(
            level,
            f"Proxy {'OK' if success else 'FAIL'}: {ip}",
            account=account,
            action="PROXY_CHECK",
            details={"proxy": proxy[:30]},
            result="OK" if success else "FAIL"
        )


# Global logger instance
_logger: Optional[Logger] = None


def get_logger(name: str = "polymarket") -> Logger:
    """Get or create logger instance"""
    global _logger
    if _logger is None:
        _logger = Logger(name)
    return _logger


def init_logger(console_level: int = logging.INFO, 
                file_level: int = logging.DEBUG,
                max_size_mb: int = 50) -> Logger:
    """Initialize logger with custom settings"""
    global _logger
    _logger = Logger(
        console_level=console_level,
        file_level=file_level,
        max_size_mb=max_size_mb
    )
    return _logger
