# Marshrut Bot

Telegram-бот для продажу відео-маршрутів (Хуст).

## Функції
- Оплата на карту
- Адмін-підтвердження
- Відео миттєво після оплати
- Збереження замовлень

## Деплой на Render
1. Зареєструйся на [render.com](https://render.com)
2. Import проект: `slbert1/marshrut-bot`
3. Використай `render.yaml`
4. Додай змінні:
   - `BOT_TOKEN`
   - `ADMIN_ID`
   - `ADMIN_CARD`
   - `PRICE_SINGLE=50`
   - `PRICE_ALL=150`

## Локальний запуск
```bash
pip install -r requirements.txt
python bot.py
