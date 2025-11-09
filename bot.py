import os
import time
import sqlite3
import logging
import aiohttp
import asyncio
from datetime import datetime, date
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from fastapi import FastAPI, Request, Response
from uvicorn import Config, Server

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONO_TOKEN = os.getenv('MONO_TOKEN')
ADMIN_ID = 5143085326  # Твоя личка

# === ЦЕНЫ ИЗ .env ===
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE', '250'))
PRICE_ALL = int(os.getenv('PRICE_ALL', '1000'))

# === БАЗА ДАННЫ ===
conn = sqlite3.connect('purchases.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        order_id TEXT UNIQUE,
        invoice_id TEXT,
        routes TEXT,
        amount INTEGER,
        payment_date TEXT,
        status TEXT
    )
''')
conn.commit()

# === ВИДЕО (ХУСТ) — ТОЛЬКО YouTube-ССЫЛКИ ===
VIDEOS = {
    'khust': {
        'route1': {
            'name': '№1',
            'youtube': 'https://www.youtube.com/watch?v=ABC123'  # ← ВСТАВЬ СВОЮ ССЫЛКУ
        },
        'route8': {
            'name': '№8',
            'youtube': 'https://www.youtube.com/watch?v=XYZ789'
        },
        'route6': {
            'name': '№6',
            'youtube': 'https://www.youtube.com/watch?v=DEF456'
        },
        'route2': {
            'name': '№2',
            'youtube': 'https://www.youtube.com/watch?v=GHI012'
        },
    }
}

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(level=logging.INFO)

# === Aiogram ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === FastAPI (Webhook) ===
app = FastAPI()

# === КНОПКИ МАРШРУТОВ ===
def get_routes_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_khust_all")],
    ])

# === СТАРТ ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("paid_"):
        order_id = args[1].split("_", 1)[1]
        cursor.execute("SELECT routes, status FROM purchases WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        if row and row[1] == 'success':
            await send_videos(message.from_user.id, row[0].split(','))
            return
    await message.answer(
        "Екзаменаційні маршрути — Хуст\n\n"
        "Обери маршрут:",
        reply_markup=get_routes_keyboard()
    )

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_khust_"))
async def buy(callback: types.CallbackQuery, state: FSMContext):
    route_key = callback.data.split("_")[-1]
    amount = PRICE_ALL if route_key == "all" else PRICE_SINGLE
    desc = "Всі 4 маршрути — Хуст" if route_key == "all" else f"Маршрут {VIDEOS['khust'][route_key]['name']} — Хуст"
    routes = ",".join(VIDEOS['khust'].keys()) if route_key == "all" else route_key

    user_id = callback.from_user.id
    order_id = f"{user_id}_{int(time.time())}"

    try:
        invoice_id = await create_mono_invoice(amount, order_id, desc, user_id)

        # === КНОПКИ: ПОВЕРНУТИСЯ + ОПЛАТИТЬ ===
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Повернутися в бота після оплати",
                url=f"tg://msg?text=/start paid_{order_id}&chat_id={user_id}"
            )],
            [InlineKeyboardButton(
                text="Оплатити будь-якою карткою",
                url=f"https://pay.monobank.ua/{invoice_id}"
            )]
        ])

        await callback.message.edit_text(
            f"**Оплати {desc} — {amount} грн**\n\n"
            f"Натисни кнопку нижче:\n\n"
            f"• Будь-яка карта (Visa, Mastercard, Apple Pay)\n"
            f"• Миттєве зарахування",
            reply_markup=kb,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

        cursor.execute(
            "INSERT OR REPLACE INTO purchases (user_id, order_id, invoice_id, routes, amount, payment_date, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (user_id, order_id, invoice_id, routes, amount, time.strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
    except Exception as e:
        await callback.message.edit_text(f"Помилка: {e}")

# === MONO INVOICE ===
async def create_mono_invoice(amount: int, order_id: str, desc: str, user_id: int):
    data = {
        'amount': amount * 100,
        'ccy': 980,
        'merchantPaymInfo': {'reference': order_id, 'description': desc},
        'webHookUrl': 'https://marshrut-bot-jock.onrender.com/webhook',
        'redirectUrl': f"https://t.me/ExamenPdr_bot?start=paid_{order_id}"
    }
    headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.monobank.ua/api/merchant/invoice/create', json=data, headers=headers) as resp:
            result = await resp.json()
            if 'invoiceId' in result:
                return result['invoiceId']
            raise Exception(f"Mono error: {result}")

# === ОТПРАВКА ССЫЛОК НА YouTube (ПРОСТО И ЧИСТО) ===
async def send_videos(user_id: int, routes: list):
    for route in routes:
        video = VIDEOS['khust'].get(route)
        if video and 'youtube' in video:
            await bot.send_message(
                user_id,
                f"Маршрут {video['name']} — Хуст\n\n"
                f"ДИВИСЯ ВІДЕО:\n{video['youtube']}"
            )
    await bot.send_message(user_id, "Дякую за покупку! Успіхів на іспиті!")

# === СТАТИСТИКА ===
async def send_stats():
    today = date.today().isoformat()
    month = today[:7]
    year = today[:4]

    cursor.execute("SELECT SUM(amount) FROM purchases WHERE payment_date LIKE ? AND status='success'", (f"{today}%",))
    day = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM purchases WHERE payment_date LIKE ? AND status='success'", (f"{month}%",))
    month_sum = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM purchases WHERE payment_date LIKE ? AND status='success'", (f"{year}%",))
    year_sum = cursor.fetchone()[0] or 0

    await bot.send_message(
        ADMIN_ID,
        f"СТАТИСТИКА:\nДень: {day} грн\nМісяць: {month_sum} грн\nРік: {year_sum} грн"
    )

# === WEBHOOK MONO ===
@app.post("/webhook")
async def mono_webhook(request: Request):
    data = await request.json()
    if data.get('status') == 'success':
        invoice_id = data['invoiceId']
        cursor.execute("SELECT user_id, routes, amount, order_id FROM purchases WHERE invoice_id = ? AND status='pending'", (invoice_id,))
        row = cursor.fetchone()
        if row:
            user_id, routes, amount, order_id = row
            cursor.execute("UPDATE purchases SET status='success' WHERE order_id=?", (order_id,))
            conn.commit()
            await send_videos(user_id, routes.split(','))
            await bot.send_message(
                ADMIN_ID,
                f"НОВА ОПЛАТА!\n"
                f"ID: {order_id}\n"
                f"Користувач: {user_id}\n"
                f"Маршрути: {routes}\n"
                f"Сума: {amount} грн\n"
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            await send_stats()
    return Response(status_code=200)

# === АВТО-ПРОВЕРКА (резерв) ===
async def check_pending():
    while True:
        await asyncio.sleep(10)
        cursor.execute("SELECT invoice_id, user_id, routes FROM purchases WHERE status='pending'")
        for invoice_id, user_id, routes in cursor.fetchall():
            async with aiohttp.ClientSession() as session:
                headers = {'X-Token': MONO_TOKEN}
                async with session.get(f'https://api.monobank.ua/api/merchant/invoice/status?invoiceId={invoice_id}', headers=headers) as resp:
                    data = await resp.json()
                    if data.get('status') == 'success':
                        cursor.execute("UPDATE purchases SET status='success' WHERE invoice_id=?", (invoice_id,))
                        conn.commit()
                        await send_videos(user_id, routes.split(','))
                        await send_stats()

# === ЗАПУСК ===
async def main():
    asyncio.create_task(check_pending())
    config = Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = Server(config)
    await server.serve()

if __name__ == "__main__":
    print("Бот запущен (БОЕВОЙ РЕЖИМ, YouTube-ссылки, статистика)...")
    asyncio.run(main())
