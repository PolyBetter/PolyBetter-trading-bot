#!/usr/bin/env python3
"""
PolyBetter Telegram Bot v2.0
============================
Async bot using aiogram framework.
Run: python -m bot.telegram_bot_v2
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set
from concurrent.futures import ThreadPoolExecutor
import functools

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, BaseFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest


# ==================== ACCESS CONTROL ====================

def _get_allowed_user_id() -> int:
    try:
        from core.config import load_config
        c = load_config()
        return getattr(c.telegram, "allowed_user_id", 0) or 0
    except Exception:
        return 0


class UserFilter(BaseFilter):
    """Filter: allow only specific user when allowed_user_id is set in config"""
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        allowed = _get_allowed_user_id()
        if not allowed:
            return True
        user_id = event.from_user.id if event.from_user else None
        return user_id == allowed

from core.config import load_config, save_config, Account
from core.data_api import DataAPI, DATA_API, CLOB_API
from core.client import get_clob_client, patch_httpx_for_proxy
from core.logger import get_logger

# Suppress aiogram logs
logging.getLogger("aiogram").setLevel(logging.WARNING)

logger = get_logger("telegram_bot")


def truncate_message(text: str, max_length: int = 4000) -> str:
    """
    Truncate message to fit Telegram's 4096 character limit.
    Leaves room for potential HTML tags expansion.
    """
    if len(text) <= max_length:
        return text
    
    # Find a good breaking point
    truncated = text[:max_length]
    
    # Try to break at last newline to avoid cutting mid-line
    last_newline = truncated.rfind('\n')
    if last_newline > max_length * 0.7:  # At least 70% of content
        truncated = truncated[:last_newline]
    
    return truncated + "\n\n<i>...сообщение обрезано</i>"


async def safe_edit(message: Message, text: str, reply_markup=None):
    """Edit message safely, ignoring 'message is not modified' error and handling long messages"""
    # Truncate if too long (Telegram limit is 4096)
    text = truncate_message(text, 4000)
    
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def safe_send(bot: Bot, chat_id: int, text: str, reply_markup=None):
    """Send message safely, handling long messages by splitting"""
    # Truncate if too long
    text = truncate_message(text, 4000)
    
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "MESSAGE_TOO_LONG" in str(e):
            # Emergency truncation
            text = text[:3500] + "\n\n<i>...сообщение обрезано</i>"
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        raise


# Thread pool for sync operations (increased for parallel account processing)
_executor = ThreadPoolExecutor(max_workers=5)

# Timeout for data operations (seconds) - increased for slow proxies
DATA_TIMEOUT = 45

# Lock for CLOB client operations
import threading
_clob_lock = threading.Lock()

# Track active operations for cancellation
_active_operations: Dict[int, asyncio.Task] = {}  # chat_id -> task


async def run_sync(func, *args, timeout: float = DATA_TIMEOUT):
    """Run sync function in thread pool with timeout"""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, functools.partial(func, *args)),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Operation timed out after {timeout}s")


# ==================== KEYBOARDS ====================

def main_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Балансы", callback_data="balances"),
        InlineKeyboardButton(text="📊 Позиции", callback_data="positions")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Профит x5+", callback_data="profit"),
        InlineKeyboardButton(text="📋 Ордера", callback_data="orders")
    )
    builder.row(
        InlineKeyboardButton(text="📝 Лимитные", callback_data="limit_orders"),
        InlineKeyboardButton(text="📈 Статистика", callback_data="stats")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Закрыть профит", callback_data="close_profit")
    )
    builder.row(
        InlineKeyboardButton(text="🔧 Инструменты", callback_data="tools"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")
    )
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    """Simple back button"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Меню", callback_data="menu"))
    return builder.as_markup()


def loading_keyboard() -> InlineKeyboardMarkup:
    """Loading indicator with cancel button"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_op"))
    return builder.as_markup()


def back_with_refresh_keyboard(action: str) -> InlineKeyboardMarkup:
    """Back button with refresh"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=action),
        InlineKeyboardButton(text="🔙 Меню", callback_data="menu")
    )
    return builder.as_markup()


def profit_keyboard() -> InlineKeyboardMarkup:
    """Profit actions keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Закрыть ВСЕ профит", callback_data="close_profit_confirm")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="profit"),
        InlineKeyboardButton(text="🔙 Меню", callback_data="menu")
    )
    return builder.as_markup()


def settings_keyboard(sell_type: str, multiplier: float, auto_close: bool, pnl_threshold: float) -> InlineKeyboardMarkup:
    """Settings keyboard with current values"""
    builder = InlineKeyboardBuilder()
    
    sell_icon = "🟢" if sell_type == "market" else "🔵"
    auto_icon = "🟢" if auto_close else "🔴"
    
    builder.row(
        InlineKeyboardButton(text=f"{sell_icon} Продажа: {sell_type.upper()}", callback_data="toggle_sell_type")
    )
    builder.row(
        InlineKeyboardButton(text=f"🎯 Мин. профит: x{multiplier:.0f}", callback_data="change_multiplier")
    )
    builder.row(
        InlineKeyboardButton(text=f"{auto_icon} Авто-закрытие", callback_data="toggle_auto_close"),
        InlineKeyboardButton(text=f"💵 Порог: ${pnl_threshold:.0f}", callback_data="change_pnl_threshold")
    )
    builder.row(InlineKeyboardButton(text="🔙 Меню", callback_data="menu"))
    return builder.as_markup()


def tools_keyboard() -> InlineKeyboardMarkup:
    """Tools keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔍 Анализ рынка", callback_data="tool_market_analysis"),
        InlineKeyboardButton(text="📊 Топ рынки", callback_data="tool_top_markets")
    )
    builder.row(
        InlineKeyboardButton(text="💹 P&L отчёт", callback_data="tool_pnl_report"),
        InlineKeyboardButton(text="📉 Ошибки", callback_data="tool_errors")
    )
    builder.row(
        InlineKeyboardButton(text="🧹 Очистить кэш", callback_data="tool_clear_cache"),
        InlineKeyboardButton(text="❌ Отменить ордера", callback_data="cancel_orders_menu")
    )
    builder.row(InlineKeyboardButton(text="🔙 Меню", callback_data="menu"))
    return builder.as_markup()


def cancel_orders_keyboard() -> InlineKeyboardMarkup:
    """Cancel orders confirmation keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚠️ ОТМЕНИТЬ ВСЕ", callback_data="cancel_all_orders_confirm")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="tools")
    )
    return builder.as_markup()


def build_polymarket_url(pos: Dict) -> Optional[str]:
    """
    Build Polymarket URL from position data.
    
    Data API returns these fields:
    - eventSlug: slug of the event (preferred for URL)
    - slug: slug of the market
    - conditionId: condition ID
    """
    # Prefer eventSlug for event page
    event_slug = pos.get('eventSlug')
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    
    # Fallback to market slug
    slug = pos.get('slug')
    if slug:
        return f"https://polymarket.com/event/{slug}"
    
    return None


def alert_keyboard(polymarket_url: Optional[str] = None) -> InlineKeyboardMarkup:
    """Keyboard for alerts with optional Polymarket link"""
    builder = InlineKeyboardBuilder()
    
    if polymarket_url:
        builder.row(InlineKeyboardButton(text="Open on Polymarket", url=polymarket_url))
    
    builder.row(InlineKeyboardButton(text="🔙 Меню", callback_data="menu"))
    return builder.as_markup()


# ==================== BOT CLASS ====================

class PolyBetterBot:
    """Main bot class"""
    
    def __init__(self):
        config = load_config()
        tg = config.telegram
        
        self.bot_token = tg.bot_token
        self.chat_id = tg.chat_id
        self.min_multiplier = tg.min_profit_multiplier
        self.monitor_interval = tg.monitor_interval_seconds  # Now 60 seconds by default
        self.auto_close_enabled = tg.auto_close_enabled
        self.auto_close_pnl = tg.auto_close_pnl
        self.sell_order_type = config.settings.sell_order_type
        
        # Anti-spam: track last notified PnL per position
        # Key: pos_key, Value: last PnL threshold when notified
        self.notified_pnl_thresholds: Dict[str, float] = {}
        
        # Minimum PnL for notification ($1)
        self.min_pnl_for_alert = 1.0
        
        # Periodic stats: track previous values for delta calculation
        # Key: account_name, Value: {usdc, positions_count, orders_count, pnl, timestamp}
        self.previous_stats: Dict[str, dict] = {}
        
        # Low balance notification tracking (to avoid spam)
        self.low_balance_notified: Set[str] = set()
        
        # Periodic stats interval (30 minutes)
        self.stats_interval_seconds = 1800  # 30 min
        
        # Create bot and dispatcher (aiogram 3.7.0+)
        self.bot = Bot(
            token=self.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()
        self.router = Router()
        
        # Apply user filter to all handlers (only ALLOWED_USER_ID can use bot)
        self.router.message.filter(UserFilter())
        self.router.callback_query.filter(UserFilter())
        
        self.dp.include_router(self.router)
        
        # Register handlers
        self._register_handlers()
        
        aid = _get_allowed_user_id()
        if aid:
            logger.info(f"Access restricted to user_id: {aid}", action="ACCESS_CONTROL")
        
        logger.info(
            f"Bot initialized: chat_id={self.chat_id}, interval={self.monitor_interval}s",
            action="BOT_INIT"
        )
    
    def _register_handlers(self):
        """Register all message and callback handlers"""
        
        # Commands
        @self.router.message(Command("start", "menu"))
        async def cmd_start(message: Message):
            await self._save_chat_id(message.chat.id)
            await self._send_main_menu(message.chat.id)
        
        @self.router.message(Command("balance", "balances"))
        async def cmd_balance(message: Message):
            msg = await message.answer("⏳ <b>Загрузка балансов...</b>\n<i>Ожидание ~15 сек</i>", reply_markup=back_keyboard())
            try:
                text = await self._get_balances()
                await safe_edit(msg, text, reply_markup=back_with_refresh_keyboard("balances"))
            except Exception as e:
                await safe_edit(msg, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
        
        @self.router.message(Command("positions"))
        async def cmd_positions(message: Message):
            msg = await message.answer("⏳ <b>Загрузка позиций...</b>\n<i>Ожидание ~15 сек</i>", reply_markup=back_keyboard())
            try:
                text = await self._get_positions()
                await safe_edit(msg, text, reply_markup=back_with_refresh_keyboard("positions"))
            except Exception as e:
                await safe_edit(msg, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
        
        @self.router.message(Command("profit"))
        async def cmd_profit(message: Message):
            msg = await message.answer("⏳ <b>Поиск профитных...</b>\n<i>Ожидание ~15 сек</i>", reply_markup=back_keyboard())
            try:
                text = await self._get_profit_positions()
                await safe_edit(msg, text, reply_markup=profit_keyboard())
            except Exception as e:
                await safe_edit(msg, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
        
        @self.router.message(Command("orders"))
        async def cmd_orders(message: Message):
            msg = await message.answer("⏳ <b>Загрузка ордеров...</b>\n<i>Ожидание ~15 сек</i>", reply_markup=back_keyboard())
            try:
                text = await self._get_orders()
                await safe_edit(msg, text, reply_markup=back_with_refresh_keyboard("orders"))
            except Exception as e:
                await safe_edit(msg, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
        
        @self.router.message(Command("help"))
        async def cmd_help(message: Message):
            text = """<b>📖 Команды:</b>

/start - Главное меню
/balance - Балансы (USDC + позиции + PnL)
/positions - Все позиции
/profit - Профитные (x5+)
/orders - Все ордера
/settings - Настройки

<b>🤖 Мониторинг:</b>
• Проверка каждые 60 секунд
• Авто-уведомления о профите
• Авто-закрытие при достижении PnL"""
            await message.answer(text, reply_markup=back_keyboard())
        
        # Callbacks
        @self.router.callback_query(F.data == "menu")
        async def cb_menu(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            # Cancel any active operation when returning to menu
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
                _active_operations.pop(chat_id, None)
            await self._send_main_menu(chat_id, edit_message=callback.message)
        
        @self.router.callback_query(F.data == "refresh")
        async def cb_refresh(callback: CallbackQuery):
            await callback.answer("✅")
            await self._send_main_menu(callback.message.chat.id, edit_message=callback.message)
        
        @self.router.callback_query(F.data == "cancel_op")
        async def cb_cancel_op(callback: CallbackQuery):
            """Cancel active operation"""
            chat_id = callback.message.chat.id
            if chat_id in _active_operations:
                task = _active_operations[chat_id]
                if not task.done():
                    task.cancel()
                del _active_operations[chat_id]
                await callback.answer("❌ Отменено")
                await safe_edit(callback.message, "🚫 <b>Операция отменена</b>", reply_markup=back_keyboard())
            else:
                await callback.answer("Нет активных операций")
                await self._send_main_menu(chat_id, edit_message=callback.message)
        
        @self.router.callback_query(F.data == "balances")
        async def cb_balances(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            # Cancel any existing operation
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            # Show loading with cancel button
            await safe_edit(callback.message, "⏳ <b>Загрузка балансов...</b>\n\n<i>Каждый аккаунт = отдельное сообщение</i>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            # Create background task
            async def do_work():
                try:
                    await self._send_balances_per_account(chat_id)
                    summary = await self._get_balances_summary()
                    await safe_edit(callback.message, summary, reply_markup=back_with_refresh_keyboard("balances"))
                except asyncio.CancelledError:
                    pass  # Cancelled, message already updated
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "positions")
        async def cb_positions(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            await safe_edit(callback.message, "⏳ <b>Загрузка позиций...</b>\n\n<i>Ожидание ~15 сек</i>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            async def do_work():
                try:
                    text = await self._get_positions()
                    await safe_edit(callback.message, text, reply_markup=back_with_refresh_keyboard("positions"))
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "profit")
        async def cb_profit(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            await safe_edit(callback.message, "⏳ <b>Поиск профитных позиций...</b>\n\n<i>Ожидание ~15 сек</i>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            async def do_work():
                try:
                    text = await self._get_profit_positions()
                    await safe_edit(callback.message, text, reply_markup=profit_keyboard())
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "orders")
        async def cb_orders(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            await safe_edit(callback.message, "⏳ <b>Загрузка ордеров...</b>\n\n<i>Каждый аккаунт = отдельное сообщение</i>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            async def do_work():
                try:
                    await self._send_orders_per_account(chat_id)
                    summary = await self._get_orders_summary()
                    await safe_edit(callback.message, summary, reply_markup=back_with_refresh_keyboard("orders"))
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "limit_orders")
        async def cb_limit_orders(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            await safe_edit(callback.message, "⏳ <b>Загрузка лимитных ордеров...</b>\n\n<i>Каждый аккаунт = отдельное сообщение</i>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            async def do_work():
                try:
                    await self._send_orders_per_account(chat_id)
                    summary = await self._get_orders_summary()
                    await safe_edit(callback.message, summary, reply_markup=back_with_refresh_keyboard("limit_orders"))
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "stats")
        async def cb_stats(callback: CallbackQuery):
            await callback.answer()
            chat_id = callback.message.chat.id
            
            if chat_id in _active_operations:
                _active_operations[chat_id].cancel()
            
            await safe_edit(callback.message, "⏳ <b>Загрузка статистики...</b>\n\n💡 Нажмите Отмена чтобы прервать", reply_markup=loading_keyboard())
            
            async def do_work():
                try:
                    text = await self._get_stats()
                    await safe_edit(callback.message, text, reply_markup=back_with_refresh_keyboard("stats"))
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    await safe_edit(callback.message, f"❌ Ошибка: {str(e)[:50]}", reply_markup=back_keyboard())
                finally:
                    _active_operations.pop(chat_id, None)
            
            _active_operations[chat_id] = asyncio.create_task(do_work())
        
        @self.router.callback_query(F.data == "settings")
        async def cb_settings(callback: CallbackQuery):
            await callback.answer()
            text = self._get_settings_text()
            keyboard = settings_keyboard(
                self.sell_order_type, 
                self.min_multiplier,
                self.auto_close_enabled,
                self.auto_close_pnl
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)
        
        @self.router.callback_query(F.data == "toggle_sell_type")
        async def cb_toggle_sell(callback: CallbackQuery):
            self.sell_order_type = "limit" if self.sell_order_type == "market" else "market"
            config = load_config()
            config.settings.sell_order_type = self.sell_order_type
            save_config(config)
            await callback.answer(f"Продажа: {self.sell_order_type.upper()}")
            text = self._get_settings_text()
            keyboard = settings_keyboard(
                self.sell_order_type,
                self.min_multiplier,
                self.auto_close_enabled,
                self.auto_close_pnl
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)
        
        @self.router.callback_query(F.data == "change_multiplier")
        async def cb_change_mult(callback: CallbackQuery):
            # Cycle: 3 -> 5 -> 10 -> 3
            if self.min_multiplier == 3:
                self.min_multiplier = 5
            elif self.min_multiplier == 5:
                self.min_multiplier = 10
            else:
                self.min_multiplier = 3
            config = load_config()
            config.telegram.min_profit_multiplier = self.min_multiplier
            save_config(config)
            await callback.answer(f"x{self.min_multiplier:.0f}")
            text = self._get_settings_text()
            keyboard = settings_keyboard(
                self.sell_order_type,
                self.min_multiplier,
                self.auto_close_enabled,
                self.auto_close_pnl
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)
        
        @self.router.callback_query(F.data == "toggle_auto_close")
        async def cb_toggle_auto(callback: CallbackQuery):
            self.auto_close_enabled = not self.auto_close_enabled
            config = load_config()
            config.telegram.auto_close_enabled = self.auto_close_enabled
            save_config(config)
            status = "ВКЛ" if self.auto_close_enabled else "ВЫКЛ"
            await callback.answer(f"Авто-закрытие: {status}")
            text = self._get_settings_text()
            keyboard = settings_keyboard(
                self.sell_order_type,
                self.min_multiplier,
                self.auto_close_enabled,
                self.auto_close_pnl
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)
        
        @self.router.callback_query(F.data == "change_pnl_threshold")
        async def cb_change_pnl(callback: CallbackQuery):
            # Cycle: 5 -> 10 -> 20 -> 50 -> 5
            if self.auto_close_pnl == 5:
                self.auto_close_pnl = 10
            elif self.auto_close_pnl == 10:
                self.auto_close_pnl = 20
            elif self.auto_close_pnl == 20:
                self.auto_close_pnl = 50
            else:
                self.auto_close_pnl = 5
            config = load_config()
            config.telegram.auto_close_pnl = self.auto_close_pnl
            save_config(config)
            await callback.answer(f"${self.auto_close_pnl:.0f}+")
            text = self._get_settings_text()
            keyboard = settings_keyboard(
                self.sell_order_type,
                self.min_multiplier,
                self.auto_close_enabled,
                self.auto_close_pnl
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)
        
        @self.router.callback_query(F.data == "tools")
        async def cb_tools(callback: CallbackQuery):
            await callback.answer()
            text = """<b>🔧 ИНСТРУМЕНТЫ</b>

<b>🔍 Анализ рынка</b> - проверить конкретный маркет
<b>📊 Топ рынки</b> - лучшие рынки по объёму
<b>💹 P&L отчёт</b> - подробный отчёт по прибыли
<b>📉 Ошибки</b> - последние ошибки
<b>🧹 Очистить кэш</b> - сбросить кэш"""
            await safe_edit(callback.message, text, reply_markup=tools_keyboard())
        
        @self.router.callback_query(F.data == "tool_top_markets")
        async def cb_top_markets(callback: CallbackQuery):
            await callback.answer("⏳ Загрузка...")
            text = await self._get_top_markets()
            await safe_edit(callback.message, text, reply_markup=tools_keyboard())
        
        @self.router.callback_query(F.data == "tool_pnl_report")
        async def cb_pnl_report(callback: CallbackQuery):
            await callback.answer("⏳")
            text = await self._get_pnl_report()
            await safe_edit(callback.message, text, reply_markup=tools_keyboard())
        
        @self.router.callback_query(F.data == "close_profit")
        async def cb_close_profit_menu(callback: CallbackQuery):
            await callback.answer()
            text = await self._get_profit_positions()
            await safe_edit(callback.message, text, reply_markup=profit_keyboard())
        
        @self.router.callback_query(F.data == "close_profit_confirm")
        async def cb_close_profit(callback: CallbackQuery):
            await callback.answer("🔄 Закрываю...")
            result = await self._close_all_profit()
            await safe_edit(callback.message, result, reply_markup=back_keyboard())
        
        @self.router.callback_query(F.data == "cancel_orders_menu")
        async def cb_cancel_orders_menu(callback: CallbackQuery):
            await callback.answer()
            text = await self._get_orders_summary_for_cancel()
            await safe_edit(callback.message, text, reply_markup=cancel_orders_keyboard())
        
        @self.router.callback_query(F.data == "cancel_all_orders_confirm")
        async def cb_cancel_all_orders(callback: CallbackQuery):
            await callback.answer("⏳ Отменяю...")
            result = await self._cancel_all_orders()
            await safe_edit(callback.message, result, reply_markup=back_keyboard())
    
    async def _get_orders_summary_for_cancel(self) -> str:
        """Get summary of orders for cancel confirmation"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет активных аккаунтов"
        
        lines = ["<b>❌ ОТМЕНА ОРДЕРОВ</b>\n"]
        total_orders = 0
        
        tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception) or result.get('error'):
                continue
            
            orders = result.get('orders', [])
            if orders:
                buy = sum(1 for o in orders if o.get('side') == 'BUY')
                sell = len(orders) - buy
                lines.append(f"📍 <b>{result['name']}</b>: {len(orders)} (🟢{buy} 🔴{sell})")
                total_orders += len(orders)
        
        if total_orders == 0:
            return "✅ Нет открытых ордеров!"
        
        lines.append(f"\n━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>ИТОГО: {total_orders} ордеров</b>")
        lines.append(f"\n⚠️ <b>Это отменит ВСЕ ордера!</b>")
        
        return "\n".join(lines)
    
    async def _cancel_all_orders(self) -> str:
        """Cancel all orders for all accounts"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет активных аккаунтов"
        
        lines = ["<b>❌ РЕЗУЛЬТАТ ОТМЕНЫ</b>\n"]
        total_cancelled = 0
        errors = 0
        
        for acc in accounts:
            try:
                if acc.proxy:
                    patch_httpx_for_proxy(acc.proxy, force=True)
                
                with _clob_lock:
                    client = get_clob_client(acc, force_new=True)
                    orders = client.get_orders()
                    
                    if not orders:
                        continue
                    
                    result = client.cancel_all()
                    cancelled = len(orders)
                    total_cancelled += cancelled
                    lines.append(f"✅ {acc.name}: отменено {cancelled}")
                    
            except Exception as e:
                errors += 1
                lines.append(f"❌ {acc.name}: {str(e)[:25]}")
        
        lines.append(f"\n━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>Отменено: {total_cancelled}</b>")
        if errors:
            lines.append(f"<b>Ошибок: {errors}</b>")
        
        return "\n".join(lines)
    
    async def _save_chat_id(self, chat_id: int):
        """Save chat ID to config"""
        chat_id_str = str(chat_id)
        if chat_id_str != self.chat_id:
            self.chat_id = chat_id_str
            config = load_config()
            config.telegram.chat_id = chat_id_str
            save_config(config)
            logger.info(f"Saved chat_id: {chat_id_str}", action="CHAT_ID_SAVED")
    
    async def _send_main_menu(self, chat_id: int, edit_message: Message = None):
        """Send main menu"""
        config = load_config()
        accounts = [Account.from_dict(a) for a in config.to_dict()['accounts']]
        enabled = sum(1 for a in accounts if a.enabled)
        
        text = f"""<b>PolyBetter Bot v2.0</b>

📊 <b>Аккаунтов:</b> {len(accounts)} (активных: {enabled})
🎯 <b>Мин. профит:</b> x{self.min_multiplier:.0f}
💹 <b>Тип продажи:</b> {self.sell_order_type.upper()}
⏰ <b>Мониторинг:</b> каждые {self.monitor_interval}с

<i>Выберите действие:</i>"""
        
        if edit_message:
            await safe_edit(edit_message, text, reply_markup=main_keyboard())
        else:
            await safe_send(self.bot, chat_id, text, reply_markup=main_keyboard())
    
    def _get_accounts(self) -> List[Account]:
        """Get enabled accounts"""
        config = load_config()
        return [
            Account.from_dict(a) 
            for a in config.to_dict()['accounts'] 
            if a.get('enabled', False) and a.get('api_key')
        ]
    
    def _fetch_account_balance_sync(self, acc: Account) -> dict:
        """Sync function to fetch single account balance (runs in thread)"""
        result = {
            'name': acc.name,
            'usdc': 0,
            'positions': [],
            'verified_positions': [],  # Positions with verified prices via orderbook
            'error': None
        }
        
        try:
            # USDC balance via CLOB (with lock to avoid race conditions)
            try:
                with _clob_lock:
                    if acc.proxy:
                        patch_httpx_for_proxy(acc.proxy, force=True)
                    client = get_clob_client(acc, force_new=True)  # Force new client
                    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                    collateral = client.get_balance_allowance(
                        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                    )
                    result['usdc'] = float(collateral.get('balance', 0)) / 1e6
            except Exception as e:
                logger.warning(f"USDC balance error: {e}", account=acc.name)
            
            # Positions via Data API (no proxy needed, no lock)
            data_api = DataAPI()
            positions = data_api.get_all_positions(acc.proxy_wallet) or []
            result['positions'] = positions
            
            # Verify prices via orderbook for top positions (limit to avoid rate limits)
            verified = []
            proxy = acc.proxy if acc.proxy else None
            for pos in positions[:10]:  # Verify top 10 positions
                token_id = pos.get('asset', '')
                if not token_id or len(token_id) < 20:
                    verified.append(pos)
                    continue
                
                try:
                    verified_price, is_verified = self._verify_price_via_orderbook(token_id, float(pos.get('curPrice', 0) or 0), proxy)
                    pos_copy = pos.copy()
                    if is_verified:
                        pos_copy['verifiedPrice'] = verified_price
                        pos_copy['priceVerified'] = True
                    else:
                        pos_copy['verifiedPrice'] = float(pos.get('curPrice', 0) or 0)
                        pos_copy['priceVerified'] = False
                    verified.append(pos_copy)
                except Exception:
                    verified.append(pos)
            
            result['verified_positions'] = verified
            
        except Exception as e:
            result['error'] = str(e)[:40]
        
        return result
    
    async def _get_balances(self) -> str:
        """Get balances for all accounts with timeout"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет активных аккаунтов"
        
        lines = ["<b>💰 БАЛАНСЫ</b>\n"]
        total_usdc = 0
        total_positions = 0
        total_pnl = 0
        
        # Fetch all accounts in parallel with timeout
        tasks = []
        for acc in accounts:
            tasks.append(run_sync(self._fetch_account_balance_sync, acc, timeout=DATA_TIMEOUT))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            acc = accounts[i]
            
            if isinstance(result, Exception):
                error_msg = "⏱ Таймаут" if isinstance(result, TimeoutError) else str(result)[:30]
                lines.append(f"<b>{acc.name}</b>: ❌ {error_msg}\n")
                continue
            
            if result.get('error'):
                lines.append(f"<b>{acc.name}</b>: ❌ {result['error']}\n")
                continue
            
            usdc_balance = result['usdc']
            positions = result['positions']
            verified_positions = result.get('verified_positions', positions)
            
            pos_value = 0
            acc_pnl = 0
            verified_count = 0
            for p in verified_positions:
                size = float(p.get('size', 0) or 0)
                # Use verified price if available, otherwise fallback to curPrice
                cur_price = float(p.get('verifiedPrice', 0) or p.get('curPrice', 0) or 0)
                avg_price = float(p.get('avgPrice', 0) or p.get('price', 0) or 0)
                value = size * cur_price
                cost = size * avg_price
                pos_value += value
                acc_pnl += value - cost
                if p.get('priceVerified'):
                    verified_count += 1
            
            total_usdc += usdc_balance
            total_positions += pos_value
            total_pnl += acc_pnl
            
            pnl_emoji = "🟢" if acc_pnl >= 0 else "🔴"
            verified_mark = f" ✓{verified_count}" if verified_count > 0 else ""
            
            lines.append(f"<b>📍 {acc.name}</b>")
            lines.append(f"   💵 USDC: <b>${usdc_balance:.2f}</b>")
            lines.append(f"   📈 Позиций: {len(positions)} (${pos_value:.2f}){verified_mark}")
            lines.append(f"   {pnl_emoji} PnL: <b>${acc_pnl:+.2f}</b>")
            lines.append(f"   💎 Всего: <b>${usdc_balance + pos_value:.2f}</b>\n")
        
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>📊 ИТОГО:</b>")
        lines.append(f"   💵 USDC: <b>${total_usdc:.2f}</b>")
        lines.append(f"   📈 Позиции: <b>${total_positions:.2f}</b>")
        lines.append(f"   {pnl_emoji} PnL: <b>${total_pnl:+.2f}</b>")
        lines.append(f"   💎 <b>ВСЕГО: ${total_usdc + total_positions:.2f}</b>")
        
        return "\n".join(lines)
    
    async def _send_balances_per_account(self, chat_id: int):
        """Send separate message for each account's balance"""
        accounts = self._get_accounts()
        if not accounts:
            return
        
        # Fetch all accounts in parallel
        tasks = [run_sync(self._fetch_account_balance_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            acc = accounts[i]
            
            if isinstance(result, Exception):
                error_msg = "⏱ Таймаут" if isinstance(result, TimeoutError) else str(result)[:30]
                await self.bot.send_message(chat_id, f"<b>📍 {acc.name}</b>\n❌ {error_msg}")
                continue
            
            if result.get('error'):
                await self.bot.send_message(chat_id, f"<b>📍 {acc.name}</b>\n❌ {result['error']}")
                continue
            
            usdc_balance = result['usdc']
            positions = result['positions']
            verified_positions = result.get('verified_positions', positions)
            
            pos_value = 0
            acc_pnl = 0
            verified_count = 0
            for p in verified_positions:
                size = float(p.get('size', 0) or 0)
                cur_price = float(p.get('verifiedPrice', 0) or p.get('curPrice', 0) or 0)
                avg_price = float(p.get('avgPrice', 0) or p.get('price', 0) or 0)
                value = size * cur_price
                cost = size * avg_price
                pos_value += value
                acc_pnl += value - cost
                if p.get('priceVerified'):
                    verified_count += 1
            
            pnl_emoji = "🟢" if acc_pnl >= 0 else "🔴"
            total = usdc_balance + pos_value
            verified_mark = f" ✓{verified_count}" if verified_count > 0 else ""
            
            text = (
                f"<b>📍 {acc.name}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 USDC: <b>${usdc_balance:.2f}</b>\n"
                f"📈 Позиций: {len(positions)} (${pos_value:.2f}){verified_mark}\n"
                f"{pnl_emoji} PnL: <b>${acc_pnl:+.2f}</b>\n"
                f"💎 Всего: <b>${total:.2f}</b>"
            )
            
            await self.bot.send_message(chat_id, text)
            await asyncio.sleep(0.1)  # Small delay between messages
    
    async def _get_balances_summary(self) -> str:
        """Get summary of all balances (short version)"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет активных аккаунтов"
        
        tasks = [run_sync(self._fetch_account_balance_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        total_usdc = 0
        total_positions = 0
        total_pnl = 0
        
        for result in results:
            if isinstance(result, Exception) or result.get('error'):
                continue
            
            total_usdc += result['usdc']
            verified_positions = result.get('verified_positions', result['positions'])
            for p in verified_positions:
                size = float(p.get('size', 0) or 0)
                cur_price = float(p.get('verifiedPrice', 0) or p.get('curPrice', 0) or 0)
                avg_price = float(p.get('avgPrice', 0) or p.get('price', 0) or 0)
                total_positions += size * cur_price
                total_pnl += (size * cur_price) - (size * avg_price)
        
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        return (
            f"<b>💰 ИТОГО ({len(accounts)} аккаунтов)</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 USDC: <b>${total_usdc:.2f}</b>\n"
            f"📈 Позиции: <b>${total_positions:.2f}</b>\n"
            f"{pnl_emoji} PnL: <b>${total_pnl:+.2f}</b>\n"
            f"💎 <b>ВСЕГО: ${total_usdc + total_positions:.2f}</b>\n\n"
            f"<i>↑ Детали по каждому аккаунту выше</i>"
        )
    
    def _fetch_positions_sync(self, acc: Account) -> dict:
        """Sync function to fetch positions with price verification (runs in thread)"""
        try:
            data_api = DataAPI(proxy=acc.proxy if acc.proxy else None)
            positions = data_api.get_all_positions(acc.proxy_wallet, size_threshold=0.1) or []
            
            # Verify prices via orderbook for top positions (only first 5 to reduce API calls)
            verified = []
            proxy = acc.proxy if acc.proxy else None
            for pos in positions[:5]:  # Verify top 5 positions only
                token_id = pos.get('asset', '')
                if not token_id or len(token_id) < 20:
                    verified.append(pos)
                    continue
                
                try:
                    data_api_price = float(pos.get('curPrice', 0) or 0)
                    verified_price, is_verified = self._verify_price_via_orderbook(token_id, data_api_price, proxy)
                    pos_copy = pos.copy()
                    if is_verified:
                        pos_copy['verifiedPrice'] = verified_price
                        pos_copy['priceVerified'] = True
                    else:
                        pos_copy['verifiedPrice'] = data_api_price
                        pos_copy['priceVerified'] = False
                    verified.append(pos_copy)
                except Exception:
                    verified.append(pos)
            
            # Add remaining positions without verification
            verified.extend(positions[5:])
            
            return {'name': acc.name, 'positions': verified, 'error': None}
        except Exception as e:
            return {'name': acc.name, 'positions': [], 'error': str(e)[:30]}
    
    async def _get_positions(self) -> str:
        """Get all positions with timeout"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет аккаунтов"
        
        lines = ["<b>📊 ПОЗИЦИИ</b>\n"]
        total_count = 0
        total_pnl = 0
        accounts_shown = 0
        max_accounts = 5  # Limit accounts to prevent MESSAGE_TOO_LONG
        
        # Fetch all in parallel
        tasks = [run_sync(self._fetch_positions_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if accounts_shown >= max_accounts:
                break
                
            if isinstance(result, Exception):
                error_msg = "⏱ Таймаут" if isinstance(result, TimeoutError) else str(result)[:25]
                lines.append(f"❌ {error_msg}\n")
                continue
            
            if result.get('error'):
                lines.append(f"<b>{result['name']}</b>: ❌ {result['error']}\n")
                continue
            
            positions = result['positions']
            if not positions:
                continue
            
            accounts_shown += 1
            
            # Sort by PnL
            sorted_pos = sorted(
                positions,
                key=lambda p: float(p.get('cashPnl', 0) or 0),
                reverse=True
            )
            
            lines.append(f"<b>📍 {result['name']}</b> ({len(positions)} поз.)")
            
            for pos in sorted_pos[:3]:  # Reduced from 5 to 3
                title = (pos.get('title', 'N/A') or 'N/A')[:22]
                outcome = pos.get('outcome', '?')[:3]
                size = float(pos.get('size', 0) or 0)
                cur_price = float(pos.get('verifiedPrice', 0) or pos.get('curPrice', 0) or 0)
                avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
                
                value = size * cur_price
                cost = size * avg_price
                pnl = value - cost
                
                total_count += 1
                total_pnl += pnl
                
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"  {emoji} {title} | ${pnl:+.2f}")
            
            if len(positions) > 3:
                lines.append(f"  <i>+{len(positions)-3} ещё</i>")
            lines.append("")
        
        if len(accounts) > max_accounts:
            lines.append(f"<i>...показано {max_accounts} из {len(accounts)} аккаунтов</i>\n")
        
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>Всего:</b> {total_count} поз | {pnl_emoji} <b>${total_pnl:+.2f}</b>")
        
        return "\n".join(lines)
    
    async def _get_profit_positions(self) -> str:
        """Get profit positions (x multiplier) with timeout"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет аккаунтов"
        
        lines = [f"<b>🚀 ПРОФИТ x{self.min_multiplier:.0f}+</b>\n"]
        profit_list = []
        total_pnl = 0
        
        # Fetch all in parallel
        tasks = [run_sync(self._fetch_positions_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            acc = accounts[i]
            
            if isinstance(result, Exception) or result.get('error'):
                continue
            
            for pos in result.get('positions', []):
                cur_price = float(pos.get('curPrice', 0) or 0)
                avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
                
                if avg_price <= 0 or cur_price <= 0:
                    continue
                
                multiplier = cur_price / avg_price
                
                if multiplier >= self.min_multiplier:
                    title = (pos.get('title', 'N/A') or 'N/A')[:30]
                    outcome = pos.get('outcome', '?')
                    size = float(pos.get('size', 0) or 0)
                    value = size * cur_price
                    pnl = value - (size * avg_price)
                    
                    total_pnl += pnl
                    profit_list.append({
                        "account": acc,
                        "token_id": pos.get('asset', ''),
                        "title": title,
                        "outcome": outcome,
                        "size": size,
                        "cur_price": cur_price,
                        "multiplier": multiplier,
                        "pnl": pnl
                    })
                    
                    lines.append(f"🎯 <b>x{multiplier:.1f}</b> | {acc.name}")
                    lines.append(f"   {title}")
                    lines.append(f"   {outcome} | {size:.1f}шт | ${value:.2f}")
                    lines.append(f"   PnL: <b>${pnl:+.2f}</b>\n")
        
        if not profit_list:
            return f"😔 Нет позиций с профитом x{self.min_multiplier:.0f}+"
        
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>Найдено:</b> {len(profit_list)} позиций")
        lines.append(f"<b>Общий PnL:</b> ${total_pnl:+.2f}")
        
        return "\n".join(lines)
    
    def _fetch_orders_sync(self, acc: Account) -> dict:
        """Sync function to fetch orders (runs in thread)"""
        try:
            with _clob_lock:
                if acc.proxy:
                    patch_httpx_for_proxy(acc.proxy, force=True)
                client = get_clob_client(acc, force_new=True)
                orders = client.get_orders() or []
            return {'name': acc.name, 'orders': orders, 'error': None}
        except Exception as e:
            return {'name': acc.name, 'orders': [], 'error': str(e)[:30]}
    
    async def _get_orders(self) -> str:
        """Get open orders with timeout"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет аккаунтов"
        
        lines = ["<b>📋 ОРДЕРА</b>\n"]
        total_orders = 0
        
        # Fetch all in parallel
        tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                error_msg = "⏱ Таймаут" if isinstance(result, TimeoutError) else str(result)[:25]
                lines.append(f"❌ {error_msg}\n")
                continue
            
            if result.get('error'):
                lines.append(f"<b>{result['name']}</b>: ❌ {result['error']}\n")
                continue
            
            orders = result['orders']
            if not orders:
                lines.append(f"<b>{result['name']}</b>: нет ордеров\n")
                continue
            
            buy_count = sum(1 for o in orders if o.get('side') == 'BUY')
            sell_count = len(orders) - buy_count
            
            lines.append(f"<b>📍 {result['name']}</b>")
            lines.append(f"   🟢 BUY: {buy_count} | 🔴 SELL: {sell_count}\n")
            
            total_orders += len(orders)
        
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>Всего:</b> {total_orders} ордеров")
        
        return "\n".join(lines)
    
    async def _send_orders_per_account(self, chat_id: int):
        """Send separate message for each account's orders"""
        accounts = self._get_accounts()
        if not accounts:
            return
        
        tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                error_msg = "⏱ Таймаут" if isinstance(result, TimeoutError) else str(result)[:30]
                await self.bot.send_message(chat_id, f"❌ {error_msg}")
                continue
            
            if result.get('error'):
                await self.bot.send_message(chat_id, f"<b>📍 {result['name']}</b>\n❌ {result['error']}")
                continue
            
            orders = result['orders']
            if not orders:
                await self.bot.send_message(chat_id, f"<b>📍 {result['name']}</b>\n📋 Нет открытых ордеров")
                continue
            
            buy_orders = [o for o in orders if o.get('side') == 'BUY']
            sell_orders = [o for o in orders if o.get('side') == 'SELL']
            
            lines = [
                f"<b>📍 {result['name']}</b>",
                f"━━━━━━━━━━━━━━━━━━",
                f"🟢 BUY: {len(buy_orders)} | 🔴 SELL: {len(sell_orders)}"
            ]
            
            # Show top 5 orders
            if buy_orders:
                lines.append("\n<b>🟢 BUY (топ 5):</b>")
                for o in sorted(buy_orders, key=lambda x: float(x.get('price', 0)), reverse=True)[:5]:
                    price = float(o.get('price', 0))
                    size = float(o.get('original_size', 0))
                    lines.append(f"  <code>{price:.4f}</code> × {size:.0f} = ${price*size:.2f}")
            
            if sell_orders:
                lines.append("\n<b>🔴 SELL (топ 5):</b>")
                for o in sorted(sell_orders, key=lambda x: float(x.get('price', 0)))[:5]:
                    price = float(o.get('price', 0))
                    size = float(o.get('original_size', 0))
                    lines.append(f"  <code>{price:.4f}</code> × {size:.0f} = ${price*size:.2f}")
            
            await self.bot.send_message(chat_id, "\n".join(lines))
            await asyncio.sleep(0.1)
    
    async def _get_orders_summary(self) -> str:
        """Get summary of all orders"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет аккаунтов"
        
        tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        total_buy = 0
        total_sell = 0
        total_value = 0
        
        for result in results:
            if isinstance(result, Exception) or result.get('error'):
                continue
            
            for o in result.get('orders', []):
                price = float(o.get('price', 0))
                size = float(o.get('original_size', 0))
                if o.get('side') == 'BUY':
                    total_buy += 1
                else:
                    total_sell += 1
                total_value += price * size
        
        return (
            f"<b>📋 ИТОГО ОРДЕРОВ ({len(accounts)} аккаунтов)</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🟢 BUY: {total_buy}\n"
            f"🔴 SELL: {total_sell}\n"
            f"📊 Всего: {total_buy + total_sell}\n"
            f"💰 Объём: ${total_value:.2f}\n\n"
            f"<i>↑ Детали по каждому аккаунту выше</i>"
        )
    
    async def _get_limit_orders(self) -> str:
        """Get limit orders with prices (with timeout)"""
        accounts = self._get_accounts()
        if not accounts:
            return "❌ Нет аккаунтов"
        
        lines = ["<b>📝 ЛИМИТНЫЕ ОРДЕРА</b>\n"]
        total_buy = 0
        total_sell = 0
        
        # Fetch all in parallel
        tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception) or result.get('error'):
                continue
            
            orders = result.get('orders', [])
            if not orders:
                continue
            
            buy_orders = sorted([o for o in orders if o.get('side') == 'BUY'], 
                               key=lambda x: float(x.get('price', 0)), reverse=True)
            sell_orders = sorted([o for o in orders if o.get('side') == 'SELL'],
                                key=lambda x: float(x.get('price', 0)))
            
            lines.append(f"<b>📍 {result['name']}</b>")
            
            if buy_orders:
                lines.append("   <b>🟢 BUY:</b>")
                for o in buy_orders[:5]:
                    price = float(o.get('price', 0))
                    size = float(o.get('original_size', 0))
                    lines.append(f"   <code>{price:.4f}</code> × {size:.0f} = ${price*size:.2f}")
                total_buy += len(buy_orders)
            
            if sell_orders:
                lines.append("   <b>🔴 SELL:</b>")
                for o in sell_orders[:5]:
                    price = float(o.get('price', 0))
                    size = float(o.get('original_size', 0))
                    lines.append(f"   <code>{price:.4f}</code> × {size:.0f} = ${price*size:.2f}")
                total_sell += len(sell_orders)
            
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"🟢 BUY: {total_buy} | 🔴 SELL: {total_sell}")
        
        return "\n".join(lines)
    
    async def _get_stats(self) -> str:
        """Get trading statistics"""
        from trackers.csv_tracker import get_trade_tracker, get_pnl_tracker
        
        trade_tracker = get_trade_tracker()
        stats = trade_tracker.get_stats(days=7)
        
        lines = ["<b>📈 СТАТИСТИКА (7 дней)</b>\n"]
        
        lines.append(f"📋 Всего ордеров: {stats['total_orders']}")
        lines.append(f"✅ Размещено: {stats['placed']}")
        lines.append(f"💰 Исполнено: {stats['filled']}")
        lines.append(f"❌ Ошибок: {stats['failed']}")
        lines.append(f"🚫 Отменено: {stats['cancelled']}")
        lines.append(f"\n💵 Объём: ${stats['total_volume']:.2f}")
        lines.append(f"🟢 BUY: {stats['by_side']['BUY']}")
        lines.append(f"🔴 SELL: {stats['by_side']['SELL']}")
        
        if stats['errors']:
            lines.append("\n<b>Топ ошибки:</b>")
            for error, count in list(stats['errors'].items())[:3]:
                lines.append(f"• {error}: {count}")
        
        return "\n".join(lines)
    
    async def _get_top_markets(self) -> str:
        """Get top markets by volume"""
        data_api = DataAPI()
        events = data_api.get_events(closed=False, limit=50)
        
        markets = []
        for event in events:
            for m in event.get('markets', []):
                vol = float(m.get('volume', 0) or 0)
                if vol > 10000:
                    markets.append({
                        'title': m.get('question', '')[:40],
                        'volume': vol,
                        'liquidity': float(m.get('liquidity', 0) or 0)
                    })
        
        markets.sort(key=lambda x: x['volume'], reverse=True)
        
        lines = ["<b>📊 ТОП РЫНКИ</b>\n"]
        
        for i, m in enumerate(markets[:10], 1):
            lines.append(f"{i}. {m['title']}")
            lines.append(f"   Vol: ${m['volume']/1000:.0f}k | Liq: ${m['liquidity']:.0f}")
        
        return "\n".join(lines)
    
    async def _get_pnl_report(self) -> str:
        """Get P&L report"""
        from trackers.csv_tracker import get_pnl_tracker
        
        pnl_tracker = get_pnl_tracker()
        summary = pnl_tracker.get_summary()
        
        lines = ["<b>💹 P&L ОТЧЁТ</b>\n"]
        
        if summary['records_count'] == 0:
            lines.append("Нет данных P&L")
        else:
            lines.append(f"📅 Период: {summary['first_record'][:10]} - {summary['last_record'][:10]}")
            lines.append(f"📊 Записей: {summary['records_count']}")
            lines.append(f"\n💰 Начальный: ${summary['starting_equity']:.2f}")
            lines.append(f"💰 Текущий: ${summary['current_equity']:.2f}")
            
            pnl_emoji = "🟢" if summary['total_pnl'] >= 0 else "🔴"
            lines.append(f"\n{pnl_emoji} <b>P&L: ${summary['total_pnl']:+.2f}</b> ({summary['total_pnl_pct']:+.1f}%)")
        
        return "\n".join(lines)
    
    def _get_settings_text(self) -> str:
        """Get settings text"""
        auto_status = "🟢 ВКЛ" if self.auto_close_enabled else "🔴 ВЫКЛ"
        return f"""<b>⚙️ НАСТРОЙКИ</b>

📊 <b>Тип продажи:</b> {self.sell_order_type.upper()}
   • MARKET - моментальная (FOK)
   • LIMIT - лимитный ордер

🎯 <b>Мин. профит:</b> x{self.min_multiplier:.0f}

🤖 <b>Авто-закрытие:</b> {auto_status}
   • Порог PnL: <b>${self.auto_close_pnl:.0f}+</b>

⏰ <b>Мониторинг:</b> каждые {self.monitor_interval}с"""
    
    async def _close_all_profit(self) -> str:
        """Close all profit positions"""
        accounts = self._get_accounts()
        profit_list = []
        
        # Find profit positions
        for acc in accounts:
            try:
                data_api = DataAPI()
                positions = data_api.get_all_positions(acc.proxy_wallet)
                
                for pos in positions:
                    cur_price = float(pos.get('curPrice', 0) or 0)
                    avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
                    
                    if avg_price > 0 and cur_price > 0:
                        multiplier = cur_price / avg_price
                        if multiplier >= self.min_multiplier:
                            profit_list.append({
                                "account": acc,
                                "token_id": pos.get('asset', ''),
                                "title": pos.get('title', '')[:20],
                                "size": float(pos.get('size', 0) or 0),
                                "cur_price": cur_price
                            })
            except:
                pass
        
        if not profit_list:
            return "😔 Нет профитных позиций"
        
        success = 0
        errors = 0
        total_value = 0
        results = []
        
        for pos in profit_list[:10]:
            acc = pos["account"]
            ok, msg, logs = await self._close_position_with_logs(
                acc, pos["token_id"], pos["size"], pos["cur_price"]
            )
            
            if ok:
                success += 1
                total_value += pos["size"] * pos["cur_price"]
                results.append(f"✅ {pos['title']}: {msg}")
            else:
                errors += 1
                # Show short error in results, full logs will be in console
                results.append(f"❌ {pos['title']}: {msg[:30]}")
                logger.error(f"Close failed: {pos['title']} | {msg} | {logs}", action="CLOSE_FAILED")
            
            await asyncio.sleep(0.5)
        
        lines = [
            "<b>📊 Результат:</b>\n",
            f"✅ Успешно: {success}",
            f"❌ Ошибки: {errors}",
            f"💰 Сумма: ${total_value:.2f}\n",
            "<b>Детали:</b>"
        ]
        lines.extend(results[:10])
        
        return "\n".join(lines)
    
    async def _close_position(self, account: Account, token_id: str, size: float, cur_price: float) -> tuple:
        """Close a single position (simple version)"""
        ok, msg, _ = await self._close_position_with_logs(account, token_id, size, cur_price)
        return ok, msg
    
    async def _close_position_with_logs(self, account: Account, token_id: str, size: float, cur_price: float) -> tuple:
        """Close a single position with detailed logs for Telegram"""
        logs = []
        
        def log(msg: str):
            logs.append(msg)
            logger.debug(msg, action="CLOSE_POSITION", account=account.name)
        
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType, BalanceAllowanceParams, AssetType
            
            log(f"[1] Token: {token_id[:20]}...")
            log(f"[2] Size: {size:.2f} | Price: ${cur_price:.4f}")
            
            # Check if position is essentially worthless (resolved loser)
            if cur_price <= 0.01:
                log(f"[ERROR] Price too low (${cur_price:.4f}) - likely resolved market")
                return False, f"Цена ~0 (рынок resolved?)", "\n".join(logs)
            
            if account.proxy:
                log(f"[3] Proxy: {account.proxy[:30]}...")
                patch_httpx_for_proxy(account.proxy, force=True)
            else:
                log("[3] No proxy")
            
            client = get_clob_client(account)
            if not client:
                log("[ERROR] CLOB client = None")
                return False, "CLOB client error", "\n".join(logs)
            
            log("[4] CLOB client OK")
            
            # Approve
            try:
                client.update_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=token_id
                    )
                )
                log("[5] Approve OK")
            except Exception as e:
                log(f"[5] Approve warning: {e}")
            
            if self.sell_order_type == "market":
                # Market order - need orderbook
                log(f"[6] Order type: MARKET (FOK)")
                
                data_api = DataAPI(proxy=account.proxy)
                book = data_api.get_orderbook(token_id)
                
                if not book:
                    log("[ERROR] Orderbook = None")
                    return False, "Orderbook unavailable", "\n".join(logs)
                
                bids = book.get('bids', [])
                asks = book.get('asks', [])
                log(f"[7] Orderbook: {len(bids)} bids, {len(asks)} asks")
                
                if not bids:
                    log("[ERROR] No bids in orderbook!")
                    log("Cannot sell - no buyers")
                    return False, "NO BIDS (нет покупателей)", "\n".join(logs)
                
                best_bid = float(bids[0].get('price', 0))
                bid_size = float(bids[0].get('size', 0))
                log(f"[8] Best bid: ${best_bid:.4f} x {bid_size:.1f}")
                
                if best_bid <= 0:
                    log("[ERROR] Best bid price = 0")
                    return False, "bid_price=0", "\n".join(logs)
                
                # Check if enough liquidity
                total_bid_size = sum(float(b.get('size', 0)) for b in bids[:5])
                log(f"[9] Total bid liquidity (top 5): {total_bid_size:.1f}")
                
                if total_bid_size < size * 0.5:
                    log(f"[WARNING] Low liquidity: need {size:.1f}, have {total_bid_size:.1f}")
                
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=size,
                    price=best_bid,
                    side="SELL"
                )
                
                log(f"[10] Creating SELL order: {size:.1f} @ ${best_bid:.4f}")
                
                try:
                    signed = client.create_market_order(order_args)
                    log("[11] Order signed OK")
                except Exception as e:
                    log(f"[ERROR] Sign failed: {e}")
                    return False, f"Sign error: {e}", "\n".join(logs)
                
                try:
                    result = client.post_order(signed, OrderType.FOK)
                    log(f"[12] API response: {result}")
                except Exception as e:
                    log(f"[ERROR] post_order failed: {e}")
                    return False, f"API error: {e}", "\n".join(logs)
                
                if result.get('success'):
                    status = result.get('status', 'OK')
                    taking = result.get('takingAmount', 0)
                    if taking:
                        usdc = float(taking) / 1e6
                        log(f"[SUCCESS] SOLD! Got ${usdc:.2f} USDC")
                        return True, f"SOLD @ ${best_bid:.3f} → ${usdc:.2f}", "\n".join(logs)
                    log(f"[SUCCESS] Order status: {status}")
                    return True, f"SOLD @ ${best_bid:.3f}", "\n".join(logs)
                else:
                    status = result.get('status', '')
                    error_msg = result.get('errorMsg', str(result))
                    log(f"[FAILED] Status: {status} | Error: {error_msg}")
                    
                    if status in ('CANCELED', 'EXPIRED'):
                        return False, f"FOK {status} (недостаточно ликвидности)", "\n".join(logs)
                    return False, error_msg, "\n".join(logs)
            
            else:
                # Limit order
                log(f"[6] Order type: LIMIT (GTC)")
                
                try:
                    tick = float(client.get_tick_size(token_id))
                    log(f"[7] Tick size: ${tick}")
                except Exception as e:
                    tick = 0.01
                    log(f"[7] Tick size error, using 0.01: {e}")
                
                sell_price = cur_price * 0.95
                sell_price = max(tick, min(0.99, sell_price))
                sell_price = round(round(sell_price / tick) * tick, 4)
                log(f"[8] Sell price: ${sell_price:.4f} (95% of ${cur_price:.4f})")
                
                order_args = OrderArgs(
                    price=sell_price,
                    size=size,
                    side="SELL",
                    token_id=token_id
                )
                
                log(f"[9] Creating LIMIT SELL: {size:.1f} @ ${sell_price:.4f}")
                
                try:
                    signed = client.create_order(order_args)
                    log("[10] Order signed OK")
                except Exception as e:
                    log(f"[ERROR] Sign failed: {e}")
                    return False, f"Sign error: {e}", "\n".join(logs)
                
                try:
                    result = client.post_order(signed, OrderType.GTC)
                    log(f"[11] API response: {result}")
                except Exception as e:
                    log(f"[ERROR] post_order failed: {e}")
                    return False, f"API error: {e}", "\n".join(logs)
                
                if result.get('success') or result.get('orderID'):
                    order_id = result.get('orderID', 'OK')
                    log(f"[SUCCESS] Limit order placed: {order_id[:20] if order_id else 'OK'}")
                    return True, f"LIMIT @ ${sell_price:.3f}", "\n".join(logs)
                else:
                    error_msg = result.get('errorMsg', str(result))
                    log(f"[FAILED] Error: {error_msg}")
                    return False, error_msg, "\n".join(logs)
                
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logs.append(f"[EXCEPTION] {e}")
            logs.append(tb)
            logger.exception(f"Close position error: {e}", account=account.name)
            return False, str(e), "\n".join(logs)
    
    def _verify_price_via_orderbook(self, token_id: str, data_api_price: float, proxy: str = None) -> tuple[float, bool]:
        """
        Verify current price via orderbook (best_bid).

        Returns:
            tuple[float, bool]: (verified_price, is_valid)
            - verified_price: best_bid from orderbook (real sell price)
            - is_valid: True if price is verified and makes sense
        """
        # Skip verification for very short token_ids (likely invalid)
        if not token_id or len(token_id) < 20:
            return data_api_price, False
        
        try:
            data_api = DataAPI(proxy=proxy) if proxy else DataAPI()
            book = data_api.get_orderbook(token_id)

            if not book:
                # Don't log 404s as errors - just skip verification
                return data_api_price, False

            bids = book.get('bids', [])
            asks = book.get('asks', [])

            if not bids:
                # No bids = can't sell, price is essentially 0
                return 0.0, True

            best_bid = float(bids[0].get('price', 0))
            best_ask = float(asks[0].get('price', 1)) if asks else 1.0

            # Log only significant discrepancies (not every mismatch)
            if data_api_price > 0.01 and best_bid > 0 and abs(best_bid - data_api_price) / data_api_price > 0.5:
                logger.debug(
                    f"Price discrepancy: DataAPI={data_api_price:.4f}, OrderBook best_bid={best_bid:.4f}",
                    action="PRICE_MISMATCH", token=token_id[:20]
                )

            return best_bid, True

        except Exception as e:
            # Silently handle errors - don't spam logs
            return data_api_price, False

    def _simulate_sell_profit(self, token_id: str, size: float, proxy: str = None) -> dict:
        """
        Simulate selling shares and calculate expected USDC profit.
        
        Returns:
            dict with:
            - can_sell: bool - whether there's enough liquidity
            - total_usdc: float - expected USDC from selling
            - avg_price: float - weighted average sell price
            - filled_size: float - how many shares can be sold
            - slippage_pct: float - slippage from best bid
            - bid_depth: float - total bid liquidity
        """
        result = {
            'can_sell': False,
            'total_usdc': 0.0,
            'avg_price': 0.0,
            'filled_size': 0.0,
            'slippage_pct': 0.0,
            'bid_depth': 0.0
        }
        
        # Skip for invalid token_ids
        if not token_id or len(token_id) < 20:
            return result
        
        try:
            data_api = DataAPI(proxy=proxy) if proxy else DataAPI()
            book = data_api.get_orderbook(token_id)
            
            if not book:
                return result
            
            bids = book.get('bids', [])
            if not bids:
                return result
            
            best_bid = float(bids[0].get('price', 0))
            total_bid_size = sum(float(b.get('size', 0)) for b in bids)
            result['bid_depth'] = total_bid_size
            
            # Simulate filling the order through bid levels
            remaining_size = size
            total_value = 0.0
            filled_size = 0.0
            
            for bid in bids:
                bid_price = float(bid.get('price', 0))
                bid_size = float(bid.get('size', 0))
                
                if remaining_size <= 0:
                    break
                
                fill = min(remaining_size, bid_size)
                total_value += fill * bid_price
                filled_size += fill
                remaining_size -= fill
            
            if filled_size > 0:
                avg_price = total_value / filled_size
                slippage = (best_bid - avg_price) / best_bid if best_bid > 0 else 0
                
                result['can_sell'] = filled_size >= size * 0.5  # At least 50% can be sold
                result['total_usdc'] = total_value
                result['avg_price'] = avg_price
                result['filled_size'] = filled_size
                result['slippage_pct'] = slippage * 100
            
        except Exception as e:
            logger.warning(f"Sell simulation error: {e}", action="SELL_SIM_ERROR")
        
        return result

    async def _monitor_positions(self):
        """Background position monitoring task"""
        logger.info(f"Monitor started (interval: {self.monitor_interval}s)", action="MONITOR_START")

        while True:
            try:
                await asyncio.sleep(self.monitor_interval)

                if not self.chat_id:
                    continue

                accounts = self._get_accounts()

                for acc in accounts:
                    try:
                        data_api = DataAPI()
                        positions = data_api.get_all_positions(acc.proxy_wallet, size_threshold=0.1)

                        for pos in positions:
                            token_id = pos.get('asset', '')
                            if not token_id:
                                continue

                            # Get price from Data API (may be stale/incorrect)
                            data_api_price = float(pos.get('curPrice', 0) or 0)
                            avg_price = float(pos.get('avgPrice', 0) or pos.get('price', 0) or 0)
                            size = float(pos.get('size', 0) or 0)

                            # Calculate preliminary multiplier
                            if avg_price <= 0 or data_api_price <= 0:
                                continue

                            preliminary_multiplier = data_api_price / avg_price

                            # Only verify via orderbook if multiplier looks profitable
                            # This avoids unnecessary API calls
                            if preliminary_multiplier >= self.min_multiplier:
                                # Verify price via orderbook (best_bid = real sell price)
                                acc_proxy = acc.proxy if acc.proxy else None
                                verified_price, is_verified = self._verify_price_via_orderbook(token_id, data_api_price, acc_proxy)

                                if is_verified:
                                    cur_price = verified_price
                                    logger.debug(
                                        f"Price verified: DataAPI={data_api_price:.4f} → OrderBook={verified_price:.4f}",
                                        action="PRICE_VERIFIED", token=token_id[:20]
                                    )
                                else:
                                    # Can't verify - skip this alert to avoid false positives
                                    logger.debug(
                                        f"Skipping unverified profit alert: {pos.get('title', 'N/A')[:30]}",
                                        action="SKIP_UNVERIFIED"
                                    )
                                    continue
                            else:
                                cur_price = data_api_price
                            
                            if avg_price <= 0 or cur_price <= 0:
                                continue
                            
                            value = size * cur_price
                            cost = size * avg_price
                            pnl = value - cost
                            multiplier = cur_price / avg_price
                            
                            pos_key = f"{acc.name}_{token_id[:20]}"
                            
                            # Check if we should notify (anti-spam: 2x previous PnL threshold, min $1)
                            last_threshold = self.notified_pnl_thresholds.get(pos_key, 0)
                            should_notify = False
                            
                            # Skip if PnL < $1
                            if pnl >= self.min_pnl_for_alert:
                                if last_threshold == 0:
                                    # First time - notify if multiplier reached
                                    should_notify = multiplier >= self.min_multiplier
                                else:
                                    # Already notified - only notify at 2x previous PnL
                                    should_notify = pnl >= last_threshold * 2
                            
                            # Auto-close check
                            if self.auto_close_enabled and pnl >= self.auto_close_pnl:
                                # Check if market is resolved (redeemable) or price is essentially 0
                                is_redeemable = pos.get('redeemable', False)
                                if is_redeemable:
                                    logger.debug(f"Skipping auto-close for redeemable position: {pos.get('title', 'N/A')}")
                                    continue
                                
                                # Skip if current price is too low (likely resolved loser)
                                if cur_price <= 0.01:
                                    logger.debug(f"Skipping auto-close for near-zero price: {pos.get('title', 'N/A')}, price={cur_price}")
                                    continue
                                
                                title = (pos.get('title', 'N/A') or 'N/A')[:40]
                                outcome = pos.get('outcome', '?')
                                
                                # Build Polymarket URL
                                poly_url = build_polymarket_url(pos)
                                
                                # Always notify for auto-close (but still track threshold)
                                await self.bot.send_message(
                                    self.chat_id,
                                    f"🤑 <b>АВТО-ЗАКРЫТИЕ!</b>\n\n"
                                    f"<b>{acc.name}</b>\n"
                                    f"{title}\n"
                                    f"{outcome} | {size:.1f}шт\n"
                                    f"PnL: <b>${pnl:+.2f}</b> (x{multiplier:.1f})\n\n"
                                    f"⏳ Закрываю...",
                                    reply_markup=alert_keyboard(poly_url)
                                )
                                
                                logger.info(f"Auto-closing position: {acc.name} | {title} | PnL: ${pnl:.2f}", 
                                           action="AUTO_CLOSE_START", account=acc.name, pnl=pnl)
                                
                                ok, msg, logs = await self._close_position_with_logs(acc, token_id, size, cur_price)
                                
                                # Truncate logs for Telegram (max ~2500 chars for code block)
                                logs_display = logs if len(logs) <= 2500 else logs[:2500] + "\n... (обрезано)"
                                
                                if ok:
                                    await self.bot.send_message(
                                        self.chat_id,
                                        f"✅ <b>Закрыто!</b>\n\n"
                                        f"<b>{acc.name}</b>\n"
                                        f"{title}\n"
                                        f"Результат: {msg}\n\n"
                                        f"<code>{logs_display}</code>",
                                        reply_markup=alert_keyboard(poly_url)
                                    )
                                    logger.info(f"Auto-close SUCCESS: {acc.name} | {msg}", 
                                               action="AUTO_CLOSE_SUCCESS", account=acc.name)
                                else:
                                    # Truncate error message for display
                                    msg_display = msg if len(str(msg)) <= 500 else str(msg)[:500] + "..."
                                    
                                    await self.bot.send_message(
                                        self.chat_id,
                                        f"❌ <b>Ошибка закрытия!</b>\n\n"
                                        f"<b>{acc.name}</b>\n"
                                        f"{title}\n"
                                        f"Ошибка: {msg_display}\n\n"
                                        f"<b>Логи:</b>\n<code>{logs_display}</code>",
                                        reply_markup=alert_keyboard(poly_url)
                                    )
                                    # Full error in logger
                                    logger.error(f"Auto-close FAILED: {acc.name} | {msg} | {logs}", 
                                                action="AUTO_CLOSE_FAILED", account=acc.name)
                                
                                # Update threshold to prevent re-notify
                                self.notified_pnl_thresholds[pos_key] = pnl
                            
                            # Profit notification (with anti-spam)
                            elif should_notify:
                                title = (pos.get('title', 'N/A') or 'N/A')[:40]
                                
                                # Build Polymarket URL
                                poly_url = build_polymarket_url(pos)
                                
                                # For high profit (x50+), check if there's liquidity to sell
                                # If yes, skip the alert (user can just take profit)
                                acc_proxy = acc.proxy if acc.proxy else None
                                if multiplier >= 50:
                                    sell_sim = self._simulate_sell_profit(token_id, size, acc_proxy)
                                    if sell_sim['can_sell'] and sell_sim['filled_size'] >= size * 0.8:
                                        # Good liquidity - skip spammy alert, just log it
                                        logger.info(
                                            f"Skipping x{multiplier:.0f} alert (good liquidity): {title} | "
                                            f"Can sell {sell_sim['filled_size']:.1f} for ${sell_sim['total_usdc']:.2f}",
                                            action="SKIP_HIGH_PROFIT", account=acc.name
                                        )
                                        continue
                                
                                # Run sell simulation for the alert
                                sell_sim = self._simulate_sell_profit(token_id, size, acc_proxy)
                                
                                next_threshold = pnl * 2
                                
                                # Build alert message with sell simulation
                                sim_text = ""
                                if sell_sim['can_sell']:
                                    sim_text = (
                                        f"\n\n💰 <b>Симуляция продажи:</b>\n"
                                        f"   Продать {sell_sim['filled_size']:.1f}шт → ~${sell_sim['total_usdc']:.2f}\n"
                                        f"   Сред. цена: ${sell_sim['avg_price']:.4f}"
                                    )
                                    if sell_sim['slippage_pct'] > 1:
                                        sim_text += f" (slippage: {sell_sim['slippage_pct']:.1f}%)"
                                elif sell_sim['bid_depth'] > 0:
                                    sim_text = f"\n\n⚠️ Низкая ликвидность: только {sell_sim['bid_depth']:.0f} в bids"
                                else:
                                    sim_text = "\n\n⚠️ Нет покупателей (bids пусты)"
                                
                                await self.bot.send_message(
                                    self.chat_id,
                                    f"🚀 <b>ПРОФИТ x{multiplier:.1f}!</b>\n\n"
                                    f"<b>{acc.name}</b>\n"
                                    f"{title}\n"
                                    f"{pos.get('outcome', '?')} | {size:.1f}шт\n"
                                    f"PnL: <b>${pnl:+.2f}</b>"
                                    f"{sim_text}\n\n"
                                    f"<i>Авто-закрытие при ${self.auto_close_pnl}+</i>\n"
                                    f"<i>След. алерт при ${next_threshold:.2f}+</i>",
                                    reply_markup=alert_keyboard(poly_url)
                                )
                                
                                # Update threshold for next notification
                                self.notified_pnl_thresholds[pos_key] = pnl
                                logger.info(f"Profit alert: {acc.name} | x{multiplier:.1f} | ${pnl:.2f}", 
                                           action="PROFIT_ALERT", account=acc.name, pnl=pnl)
                                    
                    except Exception as e:
                        logger.warning(f"Monitor error for {acc.name}: {e}")
                
                # Clean old notifications (keep last 500 entries)
                if len(self.notified_pnl_thresholds) > 1000:
                    # Keep entries with highest PnL thresholds
                    sorted_items = sorted(self.notified_pnl_thresholds.items(), 
                                         key=lambda x: x[1], reverse=True)
                    self.notified_pnl_thresholds = dict(sorted_items[:500])
                    
            except Exception as e:
                logger.exception(f"Monitor loop error: {e}")
                await asyncio.sleep(60)
    
    async def _periodic_stats_notification(self):
        """Background task to send periodic stats every 30 minutes"""
        logger.info(f"Periodic stats started (interval: {self.stats_interval_seconds}s)", action="STATS_START")
        
        while True:
            try:
                await asyncio.sleep(self.stats_interval_seconds)
                
                if not self.chat_id:
                    continue
                
                accounts = self._get_accounts()
                if not accounts:
                    continue
                
                # Fetch all account data
                tasks = [run_sync(self._fetch_account_balance_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
                order_tasks = [run_sync(self._fetch_orders_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
                
                balance_results = await asyncio.gather(*tasks, return_exceptions=True)
                order_results = await asyncio.gather(*order_tasks, return_exceptions=True)
                
                lines = ["📊 <b>СТАТИСТИКА (30 мин)</b>\n"]
                total_usdc = 0
                total_pnl = 0
                total_orders = 0
                total_positions = 0
                max_accounts_shown = 6  # Limit to prevent MESSAGE_TOO_LONG
                accounts_shown = 0
                
                for i, acc in enumerate(accounts):
                    balance_result = balance_results[i]
                    order_result = order_results[i]
                    
                    if isinstance(balance_result, Exception) or balance_result.get('error'):
                        continue
                    
                    usdc = balance_result['usdc']
                    positions = balance_result.get('verified_positions', balance_result['positions'])
                    orders = order_result.get('orders', []) if not isinstance(order_result, Exception) else []
                    
                    # Calculate PnL
                    acc_pnl = 0
                    pos_value = 0
                    for p in positions:
                        size = float(p.get('size', 0) or 0)
                        cur_price = float(p.get('verifiedPrice', 0) or p.get('curPrice', 0) or 0)
                        avg_price = float(p.get('avgPrice', 0) or p.get('price', 0) or 0)
                        value = size * cur_price
                        cost = size * avg_price
                        pos_value += value
                        acc_pnl += value - cost
                    
                    # Update stored stats
                    self.previous_stats[acc.name] = {
                        'usdc': usdc,
                        'pnl': acc_pnl,
                        'orders_count': len(orders),
                        'positions_count': len(positions),
                        'timestamp': datetime.now()
                    }
                    
                    total_usdc += usdc
                    total_pnl += acc_pnl
                    total_orders += len(orders)
                    total_positions += len(positions)
                    
                    # Only show first N accounts in message
                    if accounts_shown < max_accounts_shown:
                        pnl_emoji = "🟢" if acc_pnl >= 0 else "🔴"
                        lines.append(f"<b>{acc.name}</b>: ${usdc:.0f} | {len(positions)}p | {pnl_emoji}${acc_pnl:+.0f}")
                        accounts_shown += 1
                
                if len(accounts) > max_accounts_shown:
                    lines.append(f"<i>+{len(accounts) - max_accounts_shown} аккаунтов...</i>")
                
                # Summary
                pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
                lines.append("\n━━━━━━━━━━━━━━━━━━")
                lines.append(f"<b>ИТОГО:</b> ${total_usdc:.0f} | {total_positions}p | {total_orders}o")
                lines.append(f"{pnl_emoji} <b>PnL: ${total_pnl:+.2f}</b>")
                
                await safe_send(self.bot, self.chat_id, "\n".join(lines))
                logger.info(f"Periodic stats sent: {len(accounts)} accounts", action="STATS_SENT")
                
            except Exception as e:
                logger.exception(f"Periodic stats error: {e}")
                await asyncio.sleep(60)

    async def _check_balances_periodic(self):
        """Background task to check balances and notify about low balance"""
        logger.info("Balance check started (interval: 30 min)", action="BALANCE_CHECK_START")
        
        # Minimum balance threshold (order_amount * 10)
        from core.config import load_presets
        presets = load_presets().get("presets", {})
        default_order_amount = 0.2
        for p in presets.values():
            if p.get('order_amount'):
                default_order_amount = max(default_order_amount, p.get('order_amount', 0.2))
        min_balance = default_order_amount * 10
        
        while True:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                
                if not self.chat_id:
                    continue
                
                accounts = self._get_accounts()
                if not accounts:
                    continue
                
                # Check balance for each account
                tasks = [run_sync(self._fetch_account_balance_sync, acc, timeout=DATA_TIMEOUT) for acc in accounts]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for i, result in enumerate(results):
                    acc = accounts[i]
                    
                    if isinstance(result, Exception) or result.get('error'):
                        continue
                    
                    usdc = result['usdc']
                    
                    # Check if balance is low
                    if usdc < min_balance:
                        # Only notify once per account (until balance is restored)
                        if acc.name not in self.low_balance_notified:
                            await self.bot.send_message(
                                self.chat_id,
                                f"⚠️ <b>НИЗКИЙ БАЛАНС</b>\n\n"
                                f"<b>{acc.name}</b>\n"
                                f"💵 USDC: <b>${usdc:.2f}</b>\n"
                                f"📉 Минимум: ${min_balance:.2f}\n\n"
                                f"<i>Sniper не сможет размещать ордера.</i>\n"
                                f"<i>Пополните баланс для продолжения.</i>"
                            )
                            self.low_balance_notified.add(acc.name)
                            logger.warning(f"Low balance alert: {acc.name} = ${usdc:.2f}", 
                                          action="LOW_BALANCE", account=acc.name)
                    else:
                        # Balance restored - remove from notified set
                        if acc.name in self.low_balance_notified:
                            self.low_balance_notified.discard(acc.name)
                            logger.info(f"Balance restored: {acc.name} = ${usdc:.2f}", 
                                        action="BALANCE_RESTORED", account=acc.name)
                
            except Exception as e:
                logger.exception(f"Balance check error: {e}")
                await asyncio.sleep(60)

    async def run(self):
        """Run the bot"""
        if not self.bot_token:
            logger.error("bot_token not configured!")
            return
        
        print("\n" + "=" * 50)
        print("🤖 POLYMARKET TELEGRAM BOT v2.0")
        print("=" * 50)
        print(f"Chat ID: {self.chat_id or 'не настроен'}")
        print(f"Monitor: каждые {self.monitor_interval}s")
        print(f"Auto-close: ${self.auto_close_pnl}+ PnL")
        print("\nБот запущен! Ctrl+C для остановки\n")
        
        # Start background tasks
        asyncio.create_task(self._monitor_positions())
        asyncio.create_task(self._periodic_stats_notification())
        asyncio.create_task(self._check_balances_periodic())
        
        # Start polling with concurrency limit to prevent memory issues
        await self.dp.start_polling(self.bot, tasks_concurrency_limit=10)


async def main():
    """Entry point"""
    bot = PolyBetterBot()
    await bot.run()


def run_bot():
    """Sync entry point"""
    asyncio.run(main())


if __name__ == "__main__":
    run_bot()
