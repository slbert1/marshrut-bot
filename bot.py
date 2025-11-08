import os
import asyncio
import aiohttp
import sqlite3
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONO_TOKEN = os.getenv('MONO_TOKEN')
WEBHOOK_URL = "https://webhook.site/873bf366-974d-4a92-b93c-fe815662bcd9"  # ← ВСТАВЬ СВОЙ URL СЮДА

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === БАЗА ДАННЫХ ===
conn = sqlite3.connect('purchases.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS purchases (user_id INTEGER, order_id TEXT, routes TEXT, status TEXT)')
conn.commit()

# === ВИДЕО ===
VIDEOS = {
    'route1': {'name': '№1', 'url': 'https://youtu.be/mxtsqKmXWSI'},
    'route8': {'name': '№8', 'url': 'https://youtu.be/7VwtAAaQWE8'},
    'route6': {'name': '№6', 'url': 'https://youtu.be/RnpOEKIddZw'},
    'route2': {'name': '№2', 'url': 'https://youtu.be/RllCGT6dOPc'},
}

class Cart(StatesGroup):
    viewing = State()
    paying = State()

# === КНОПКИ ===
@dp.message(Command('start'))
async def start(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ №1 — 250 грн", callback_data="buy_route1")],
        [InlineKeyboardButton(text="2️⃣ №8 — 250 грн", callback_data="buy_route8")],
        [InlineKeyboardButton(text="3️⃣ №6 — 250 грн", callback_data="buy_route6")],
        [InlineKeyboardButton(text="4️⃣ №2 — 250 грн", callback_data="buy_route2")],
        [InlineKeyboardButton(text="Всі 4 — 1000 грн", callback_data="buy_all")]
    ])
    await message.answer("Экзаменаційні маршрути — Хуст\n\nОбери маршрут:", reply_markup=kb)
    await state.set_state(Cart.viewing)

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_"))
async def buy(callback: types.CallbackQuery, state: FSMContext):
    route_key = callback.data.split("_")[1]
    if route_key == "all":
        amount = 1000
        desc = "Всі 4 маршрути"
        routes = "route1,route8,route6,route2"
    else:
        amount = 250
        desc = VIDEOS[route_key]['name']
        routes = route_key

    user_id = callback.from_user.id
    order_id = f"{user_id}_{int(time.time())}"

    try:
        url = await create_mono_invoice(amount, order_id, desc)
        await callback.message.answer(
            f"Оплати {desc} ({amount} грн):\n{url}\n\n(Будь-яка карта)",
            disable_web_page_preview=True
        )
        cursor.execute(
            "INSERT INTO purchases (user_id, order_id, routes, status) VALUES (?, ?, ?, 'pending')",
            (user_id, order_id, routes)
        )
        conn.commit()
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
    await state.set_state(Cart.paying)

# === MONO INVOICE ===
async def create_mono_invoice(amount: int, order_id: str, desc: str):
    data = {
        'amount': amount * 100,
        'ccy': 980,
        'merchantPaymInfo': {'reference': order_id, 'description': desc},
        'webHookUrl': WEBHOOK_URL,
        'redirectUrl': f"https://t.me/MarshrutKhust_bot?start=paid_{order_id}"
    }
    headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.monobank.ua/api/merchant/invoice/create', json=data, headers=headers) as resp:
            result = await resp.json()
            if 'invoiceId' in result:
                return f"https://pay.monobank.ua/{result['invoiceId']}"
            raise Exception(f"Mono error: {result}")

# === WEBHOOK ОТ MONO ===
async def mono_webhook(request):
    data = await request.json()
    if data.get('status') == 'success':
        invoice_id = data['invoiceId']
        row = cursor.execute("SELECT user_id, routes FROM purchases WHERE order_id LIKE ? AND status='pending'", (f"%{invoice_id.split('_')[-1]}",)).fetchone()
        if row:
            user_id, routes = row
            cursor.execute("UPDATE purchases SET status='paid' WHERE user_id=?", (user_id,))
            conn.commit()

            text = "Оплата пройшла! Твої маршрути:\n\n"
            for r in routes.split(','):
                video = VIDEOS[r]
                text += f"{video['name']}: {video['url']}\n"
            await bot.send_message(user_id, text)
    return web.Response(text="OK")

# === HEALTH CHECK ДЛЯ RENDER ===
async def health(request):
    return web.Response(text="OK")

# === ЗАПУСК ===
async def main():
    print("Бот запущен...")

    # Веб-сервер для Render + Webhook
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_post('/webhook', mono_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', os.getenv('PORT', 8000))
    await site.start()

    # Запуск бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
