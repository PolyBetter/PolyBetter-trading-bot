# Polymarket Trading Tool v3.0

Полностью переработанный инструмент для торговли на Polymarket.

## 🚀 Что нового в v3.0

### ⚡ Производительность
- **Async httpx** - параллельные запросы для быстрого сканирования
- **Connection pooling** - переиспользование соединений
- **Оптимизированный кэш** - меньше повторных запросов

### 📊 Логирование
- **Полные логи в файл** - ничего не обрезается
- **JSON логи** - для парсинга и анализа
- **Отдельный файл ошибок** - быстрый поиск проблем
- **Чистый вывод в терминал** - только важное

### 📈 CSV Трекинг
- **trade_history.csv** - все ордера с полными деталями
- **positions_*.csv** - снимки позиций
- **pnl_*.csv** - трекинг P&L

### 🎯 Стратегии
- **LimitSniper** - стандартный спам ордеров
- **SmartSniper** - умная фильтрация с скорингом рынков

### 🤖 Telegram бот
- **aiogram 3.x** - современный async фреймворк
- **Мониторинг каждую минуту** (было 10 минут)
- **Кнопка "Назад"** на каждом экране
- **Инструменты** - анализ, статистика

## 📁 Структура проекта

```
newpoly/
├── main.py                 # Главный файл запуска
├── config.json             # Конфигурация аккаунтов
├── presets.json            # Пресеты стратегий
│
├── core/                   # Ядро системы
│   ├── config.py          # Загрузка конфигурации
│   ├── logger.py          # Система логирования
│   ├── client.py          # CLOB клиент
│   └── data_api.py        # Data API клиент
│
├── strategies/             # Торговые стратегии
│   ├── base.py            # Базовый класс
│   ├── sniper.py          # Limit Sniper
│   └── smart_sniper.py    # Smart Sniper
│
├── trackers/              # Трекинг данных
│   └── csv_tracker.py     # CSV логирование
│
├── bot/                   # Telegram бот
│   └── telegram_bot_v2.py # Async бот на aiogram
│
├── tools/                 # Вспомогательные инструменты
│   ├── analyzer.py        # Анализ рынков
│   └── simulator.py       # Симуляция стратегий
│
├── logs/                  # Логи (создаётся автоматически)
│   ├── polymarket.log     # Полный лог
│   ├── polymarket.json.log # JSON лог
│   └── polymarket_errors.log # Только ошибки
│
└── data/                  # Данные (создаётся автоматически)
    ├── trades_*.csv       # История ордеров
    ├── positions_*.csv    # Снимки позиций
    └── pnl_*.csv          # P&L трекинг
```

## 🔧 Установка

```bash
# Установить зависимости
pip install -r requirements_v3.txt

# Или для aiogram 3.x
pip install aiogram>=3.4.0 httpx>=0.27.0
```

## 🎮 Использование

### Интерактивное меню
```bash
python main.py
```

### Прямой запуск
```bash
# Sniper
python main.py sniper

# Smart Sniper
python main.py smart

# Telegram бот
python main.py bot

# Анализ рынков
python main.py analyze

# Симуляция
python main.py simulate
```

## ⚙️ Конфигурация

### config.json
```json
{
  "accounts": [
    {
      "name": "Account 1",
      "enabled": true,
      "private_key": "...",
      "api_key": "...",
      "api_secret": "...",
      "api_passphrase": "...",
      "proxy_wallet": "0x...",
      "proxy": "http://user:pass@host:port"
    }
  ],
  "telegram": {
    "bot_token": "...",
    "chat_id": "...",
    "min_profit_multiplier": 5,
    "monitor_interval_seconds": 60,
    "auto_close_enabled": true,
    "auto_close_pnl": 10
  },
  "settings": {
    "check_sell_liquidity": true,
    "min_bid_size": 5,
    "sell_order_type": "limit"
  }
}
```

## 📋 Пресеты

### aggressive (🔥 Агрессивный)
- Максимум ордеров, низкие пороги
- Ордер: $0.10
- Мин. объём: $5k

### medium (⚖️ Сбалансированный)
- Баланс между количеством и качеством
- Ордер: $0.20
- Мин. объём: $10k
- Блокировка спорта

### conservative (🎯 Консервативный)
- Только качественные рынки
- Ордер: $0.50
- Мин. объём: $50k
- Блокировка спорта и крипты

### smart (🧠 Умный)
- Скоринг рынков
- Анализ ликвидности
- Проверка спреда
- Ордер: $0.30

## 📊 Логи

### Файлы логов
- `logs/polymarket.log` - полный текстовый лог
- `logs/polymarket.json.log` - структурированный JSON
- `logs/polymarket_errors.log` - только ошибки

### Формат JSON лога
```json
{
  "timestamp": "2024-01-01T12:00:00",
  "level": "INFO",
  "logger": "polymarket",
  "message": "Order placed: BUY 20.00 @ $0.0100",
  "account": "Account 1",
  "action": "ORDER_PLACED",
  "details": {"token_id": "...", "side": "BUY"},
  "duration_ms": 150
}
```

## 📈 CSV Трекинг

### trades_YYYY-MM-DD.csv
Каждый ордер записывается с полными деталями:
- timestamp, account, action
- token_id, market_title, outcome
- side, price, size, value
- order_id, order_type, status
- error (полный текст!), duration_ms

### Что отслеживается
- ORDER_PLACED - ордер размещён
- ORDER_FILLED - ордер исполнен
- ORDER_FAILED - ошибка (с полным текстом!)
- ORDER_CANCELLED - ордер отменён

## 🤖 Telegram бот

### Команды
- `/start` - главное меню
- `/balance` - балансы всех аккаунтов
- `/positions` - все позиции
- `/profit` - профитные позиции x5+
- `/orders` - открытые ордера

### Функции
- Мониторинг каждые 60 секунд
- Уведомления о профите
- Авто-закрытие при PnL >= порога
- Инструменты анализа

## 🔍 Инструменты

### Анализатор рынков
```bash
python main.py analyze
# или
python -m tools.analyzer
```
- Распределение по объёму
- Анализ категорий
- Покрытие пресетов
- Поиск возможностей

### Симулятор
```bash
python main.py simulate
# или
python -m tools.simulator
```
- Monte Carlo симуляция
- Сравнение пресетов
- Оценка рисков

## 🔒 Безопасность

- Приватные ключи хранятся в config.json (добавьте в .gitignore!)
- Прокси поддерживаются для всех запросов
- Проверка IP перед торговлей

## 📝 Миграция с v2.0

1. Скопируйте `config.json` и `presets.json`
2. Установите новые зависимости: `pip install -r requirements_v3.txt`
3. Запустите: `python main.py`

Старые файлы (`polymarket_tool.py`, `telegram_bot.py`) можно удалить или оставить как бэкап.

## 🐛 Отладка

### Проблемы с прокси
Проверьте логи в `logs/polymarket_errors.log`

### Проблемы с ордерами
Смотрите `data/trades_*.csv` - там полные тексты ошибок

### Проблемы с ботом
Убедитесь что `bot_token` и `chat_id` настроены в config.json

## 📄 Лицензия

MIT License
