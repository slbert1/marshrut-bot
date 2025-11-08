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
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONO_TOKEN = os.getenv('MONO_TOKEN')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === БАЗА ДАННЫХ ===
conn = sqlite3.connect('purchases.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        order_id TEXT,
        invoice_id TEXT UNIQUE,
        routes TEXT,
        status TEXT
    )
''')
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

# === СТАРТ ===
@dp.message(Command('start'))
async def start(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith('paid_'):
        await manual_paid(message)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. №1 — 250 грн", callback_data="buy_route1")],
        [InlineKeyboardButton(text="2. №8 — 250 грн", callback_data="buy_route8")],
        [InlineKeyboardButton(text="3. №6 — 250 грн", callback_data="buy_route6")],
        [InlineKeyboardButton(text="4. №2 — 250 грн", callback_data="buy_route2")],
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
        invoice_id = await create_mono_invoice(amount, order_id, desc)
        await callback.message.answer(
            f"Оплати {desc} ({amount} грн):\nhttps://pay.monobank.ua/{invoice_id}\n\n(Будь-яка карта)",
            disable_web_page_preview=True
        )
        cursor.execute(
            "INSERT OR REPLACE INTO purchases (user_id, order_id, invoice_id, routes, status) VALUES (?, ?, ?, ?, 'pending')",
            (user_id, order_id, invoice_id, routes)
        )
        conn.commit()
        print(f"[DB] Saved: {invoice_id}")
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
        print(f"[ERROR] {e}")
    await state.set_state(Cart.paying)

# === MONO INVOICE ===
async def create_mono_invoice(amount: int, order_id: str, desc: str):
    data = {
        'amount': amount * 100,
        'ccy': 980,
        'merchantPaymInfo': {'reference': order_id, 'description': desc},
        'webHookUrl': 'https://webhook.site/873bf366-974d-4492-b93c-fe815662bcd9',
        'redirectUrl': f"https://t.me/MarshrutKhust_bot?start=paid_{order_id}"
    }
    headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.monobank.ua/api/merchant/invoice/create', json=data, headers=headers) as resp:
            result = await resp.json()
            if 'invoiceId' in result:
                invoice_id = result['invoiceId']
                print(f"[MONO] Invoice: {invoice_id}")
                return invoice_id
            raise Exception(f"Mono error: {result}")

# === ВЕБХУК (отдельный сервер) ===
async def webhook_server():
    from aiohttp import web
    async def handle(request):
        data = await request.json()
        print(f"[WEBHOOK] {data}")

        if data.get('status') == 'created':
            invoice_id = data.get('invoiceId')
            if not invoice_id:
                return web.Response(text="No invoiceId")

            row = cursor.execute(
                "SELECT user_id, routes FROM purchases WHERE invoice_id=? AND status='pending'",
                (invoice_id,)
            ).fetchone()

            if row:
                user_id, routes = row
                cursor.execute("UPDATE purchases SET status='paid' WHERE invoice_id=?", (invoice_id,))
                conn.commit()
                print(f"[DB] Paid: {invoice_id}")

                text = "Оплата пройшла! Твої маршрути:\n\n"
                for r in routes.split(','):
                    video = VIDEOS[r]
                    text += f"{video['name']}: {video['url']}\n"
                await bot.send_message(user_id, text)
                print(f"[BOT] Video sent to {user_id}")

        return web.Response(text="OK")

    app = web.Application()
    app.router.add_post('/webhook', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8000)))
    await site.start()
    print("Webhook server running...")

# === РУЧНАЯ КОМАНДА ===
async def manual_paid(message: types.Message):
    paid_id = message.text.split()[-1]
    print(f"[MANUAL] Check: {paid_id}")

    row = cursor.execute(
        "SELECT routes, status FROM purchases WHERE invoice_id=? OR order_id=?",
        (paid_id, paid_id)
    ).fetchone()

    if row and row[1] == 'paid':
        text = "Твої маршрути:\n\n"
        for r in row[0].split(','):
            video = VIDEOS[r]
            text += f"{video['name']}: {video['url']}\n"
        await message.answer(text)
    else:
        await message.answer("Оплата не підтверджена.")

# === ЗАПУСК ===
async def main():
    print("Бот запущен...")
    await asyncio.gather(
        webhook_server(),
        dp.start_polling(bot)
    )

if __name__ == '__main__':
    asyncio.run(main())
