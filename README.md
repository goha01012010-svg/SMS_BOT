# SMS Activation Telegram Bot

Бот для работы с SMS-сервисом активации через Telegram.

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python bot.py
```

## Настройка

В файле `bot.py` замени при необходимости:
- `BOT_TOKEN` — токен Telegram-бота (от @BotFather)
- `API_KEY` — X-API-Key от SMS-сервиса
- `BASE_URL` — базовый URL API

## Как пользоваться

1. Напиши боту `/start`
2. Отправь номер телефона в формате `+79001234567`
3. Дождись SMS и отправь код боту
4. Готово!
