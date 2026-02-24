# 📖 Руководство по настройке config.json

## 🚀 Быстрый старт

### Шаг 1: Создайте config.json
```bash
# Скопируйте шаблон
cp config_empty.json config.json
```

### Шаг 2: Получите API ключи от Polymarket

#### 2.1. Подготовка
- Установите MetaMask (если еще нет)
- Убедитесь что в кошельке есть USDC на Polygon
- Убедитесь что кошелек подключен к сайту polymarket.com

#### 2.2. Получение API ключей
1. Откройте: https://clob.polymarket.com/auth/derive-api-key
2. Нажмите **"Connect Wallet"** → MetaMask
3. Подпишите сообщение в MetaMask
4. Скопируйте **3 ключа**:
   - `apiKey` (UUID формат)
   - `secret` (base64 строка)
   - `passphrase` (hex строка)

#### 2.3. Получение Private Key (ОСТОРОЖНО!)
1. MetaMask → Нажмите на иконку аккаунта
2. **Account Details** → **Show Private Key**
3. Введите пароль MetaMask
4. Скопируйте private key (без `0x`)

⚠️ **ВАЖНО**: Никому не показывайте private key! Это полный доступ к кошельку!

---

## 📝 Заполнение config.json

### 1. Секция `accounts`

```json
{
  "accounts": [
    {
      "name": "Main Account",          // Любое название
      "enabled": true,                  // true = активен
      "private_key": "abc123...",       // Private key БЕЗ 0x
      "api_key": "uuid-here",           // API Key от CLOB
      "api_secret": "base64==",         // API Secret от CLOB
      "api_passphrase": "hex123...",    // API Passphrase от CLOB
      "proxy_wallet": "",               // Пусто если не используете
      "proxy": ""                       // Пусто для прямого подключения
    }
  ]
}
```

#### Добавление нескольких аккаунтов:
```json
{
  "accounts": [
    {
      "name": "Account 1",
      "enabled": true,
      ...
    },
    {
      "name": "Account 2",
      "enabled": true,
      ...
    },
    {
      "name": "Account 3 (disabled)",
      "enabled": false,              // Отключен
      ...
    }
  ]
}
```

---

### 2. Прокси (опционально)

Если используете прокси:

```json
{
  "proxy": "http://username:password@ip:port"
}
```

**Примеры:**
- HTTP: `"http://user:pass@91.123.64.127:50100"`
- SOCKS5: `"socks5://user:pass@91.123.64.127:1080"`
- Без прокси: `""`

**Зачем нужен прокси?**
- Для обхода rate limits (каждый аккаунт на своем IP)
- Для работы из стран с ограничениями

---

### 3. Telegram бот (опционально)

#### 3.1. Создание бота
1. Напишите [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot`
3. Придумайте имя и username для бота
4. Скопируйте **токен** (формат: `1234567890:ABCdefGHI...`)

#### 3.2. Получение Chat ID
1. Напишите [@userinfobot](https://t.me/userinfobot) в Telegram
2. Скопируйте ваш **ID** (цифры)

#### 3.3. Заполнение:
```json
{
  "telegram": {
    "bot_token": "1234567890:ABCdefGHI...",
    "chat_id": "123456789",
    "min_profit_multiplier": 5,        // 5x ROI для уведомлений
    "monitor_interval_seconds": 60,    // Проверка каждую минуту
    "auto_close_enabled": false,       // Авто-закрытие позиций
    "auto_close_pnl": 40.0            // При прибыли $40
  }
}
```

---

### 4. Настройки (можно оставить по умолчанию)

```json
{
  "settings": {
    "parallel_requests": 50,           // Скорость сканирования
    "request_timeout": 30,             // Таймаут запросов
    "no_candidates_pause_minutes": 5,  // Пауза если нет рынков
    "cache_reset_minutes": 30,         // Сброс кеша
    "check_sell_liquidity": true,      // Проверка ликвидности
    "min_bid_size": 5.0,              // Мин. размер bid ($)
    "min_bid_count": 1,               // Мин. кол-во bid
    "sell_order_type": "limit",       // limit или market
    "log_level": "INFO",              // DEBUG/INFO/WARNING/ERROR
    "log_max_size_mb": 50             // Макс. размер лога
  }
}
```

---

## ✅ Проверка настроек

После заполнения config.json:

### 1. Проверка прокси
```bash
python main.py
# Выберите: 1) Проверка прокси
```

### 2. Проверка кошелька
```bash
python main.py
# Выберите: 2) Проверка кошелька
```

### 3. Проверка ВСЕХ аккаунтов сразу
```bash
python main.py
# Выберите: V) Проверка ВСЕХ аккаунтов
```

Вы увидите:
- ✅ IP адрес прокси
- ✅ Баланс USDC
- ✅ Количество позиций
- ✅ Количество открытых ордеров

---

## 🔒 Безопасность

### ❗ ОБЯЗАТЕЛЬНО:
1. **НЕ загружайте config.json в GitHub/GitLab**
2. Добавьте в `.gitignore`:
   ```
   config.json
   *.log
   logs/
   ```
3. Храните копию config.json в **безопасном месте**
4. Используйте разные private keys для торговли и хранения

### Рекомендации:
- Начните с **малых сумм** для теста
- Используйте **отдельный кошелек** для торговли
- Регулярно выводите прибыль на основной кошелек

---

## 🆘 Частые ошибки

### Ошибка: "Invalid API credentials"
- ✅ Проверьте что скопировали ВСЕ 3 ключа
- ✅ Убедитесь что нет лишних пробелов
- ✅ API ключи привязаны к private key - должны совпадать

### Ошибка: "Insufficient balance"
- ✅ Пополните USDC на Polygon
- ✅ Минимум ~$10-20 для начала работы

### Ошибка: "Proxy connection failed"
- ✅ Проверьте формат: `http://user:pass@ip:port`
- ✅ Проверьте что прокси работает
- ✅ Попробуйте без прокси (оставьте пустым)

### Ошибка: "Rate limit exceeded"
- ✅ Используйте прокси (разные IP для аккаунтов)
- ✅ Уменьшите `parallel_requests` в settings

---

## 📞 Поддержка

Если возникли проблемы:
1. Проверьте логи в папке `logs/`
2. Запустите с `"log_level": "DEBUG"`
3. Используйте меню проверки (V) для диагностики

---

**Удачной торговли! 🚀**
