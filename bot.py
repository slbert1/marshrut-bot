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

# === ВИДЕО — ТОЛЬКО ХУСТ ===
VIDEOS = {
    'khust': {
        'route1': {'name': '№1', 'url': 'https://youtu.be/mxtsqKmXWSI'},
        'route8': {'name': '№8', 'url': 'https://youtu.be/7VwtAAaQWE8'},
        'route6': {'name': '№6', 'url': 'https://youtu.be/RnpOEKIddZw'},
        'route2': {'name': '№2', 'url': 'https://youtu.be/RllCGT6dOPc'},
    }
}

class Cart(StatesGroup):
    viewing_routes = State()

# === СТАРТ С КНОПКОЙ "СТАРТ" ===
@dp.message(Command('start'))
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) > 1 and args[1].startswith('paid_'):
        await manual_paid(message)
        return

    was_here = cursor.execute(
        "SELECT 1 FROM purchases WHERE user_id=?", (user_id,)
    ).fetchone()

    if was_here:
        await show_khust_routes(message, state)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="СТАРТ", callback_data="begin_bot")]
    ])
    await message.answer(
        "Вітаю! Це бот з екзаменаційними маршрутами\n\n"
        "Натисни кнопку нижче — і почнемо",
        reply_markup=kb
    )

# === КНОПКА "СТАРТ" → МАРШРУТЫ ХУСТА ===
@dp.callback_query(F.data == "begin_bot")
async def begin_bot(callback: types.CallbackQuery, state: FSMContext):
    welcome_text = (
        "Вітаю! Це бот з екзаменаційними маршрутами для водіїв — Хуст\n\n"
        "Як користуватися:\n"
        "1. Обери маршрут або «Всі маршрути»\n"
        "2. Оплати будь-якою картою (250 грн за один, 1000 грн за всі)\n"
        "3. Відео прийде автоматично в цю ж переписку\n\n"
        "Після оплати — відео доступне лише тобі\n"
        "Ніхто не бачить твої дані\n\n"
        "Готовий? Обери нижче"
    )
    await callback.message.edit_text(welcome_text)
    await show_khust_routes(callback.message, state)

# === ВЫВОД МАРШРУТОВ ХУСТА ===
async def show_khust_routes(message: types.Message | types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Маршрут №1 — 250 грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text="Маршрут №8 — 250 грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text="Маршрут №6 — 250 грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text="Маршрут №2 — 250 грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text="Всі 4 маршрути — 1000 грн", callback_data="buy_khust_all")],
    ])
    text = "Маршрути — Хуст\n\nОбери:"
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)
    await state.set_state(Cart.viewing_routes)

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_khust_"))
async def buy(callback: types.CallbackQuery, state: FSMContext):
    route_key = callback.data.split("_")[-1]
    amount = 1000 if route_key == "all" else 250
    desc = "Всі 4 маршрути — Хуст" if route_key == "all" else f"Маршрут {VIDEOS['khust'][route_key]['name']} — Хуст"
    routes = ",".join(VIDEOS['khust'].keys()) if route_key == "all" else route_key

    user_id = callback.from_user.id
    order_id = f"{user_id}_{int(time.time())}"

    try:
        invoice_id = await create_mono_invoice(amount, order_id, desc)
        await callback.message.edit_text(
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
        await callback.message.edit_text(f"Помилка: {e}")
        print(f"[ERROR] {e}")

# === MONO INVOICE ===
async def create_mono_invoice(amount: int, order_id: str, desc: str):
    data = {
        'amount': amount * 100,
        'ccy': 980,
        'merchantPaymInfo': {'reference': order_id, 'description': desc},
        'webHookUrl': 'https://webhook.site/873bf366-974d-4492-b93c-fe815662bcd9',
        'redirectUrl': f"https://t.me/ExamenPdr_bot?start=paid_{order_id}"
    }
    headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.monobank.ua/api/merchant/invoice/create', json=data, headers=headers) as resp:
            result = await resp.json()
            if 'invoiceId' in result:
                return result['invoiceId']
            raise Exception(f"Mono error: {result}")

# === РУЧНАЯ ПРОВЕРКА ОПЛАТЫ ===
async def manual_paid(message: types.Message):
    paid_id = message.text.split()[-1].strip()
    row = cursor.execute(
        "SELECT routes, status FROM purchases WHERE invoice_id=? OR order_id=?",
        (paid_id, paid_id)
    ).fetchone()
    
    if not row:
        await message.answer("Замовлення не знайдено. Оплата ще обробляється.")
        return
    
    if row[1] == 'paid':
        text = "Твої маршрути:\n\n"
        for r in row[0].split(','):
            name = VIDEOS['khust'][r]['name']
            url = VIDEOS['khust'][r]['url']
            text += f"Маршрут {name}: {url}\n"
        await message.answer(text)
    else:
        await message.answer("Оплата ще обробляється. Зачекай 1-2 хв.")

# === АВТО-ПРОВЕРКА ОПЛАТ ===
async def check_pending_payments():
    while True:
        await asyncio.sleep(10)
        rows = cursor.execute("SELECT invoice_id, user_id, routes FROM purchases WHERE status='pending'").fetchall()
        for invoice_id, user_id, routes in rows:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {'X-Token': MONO_TOKEN}
                    async with session.get(f'https://api.monobank.ua/api/merchant/invoice/status?invoiceId={invoice_id}', headers=headers) as resp:
                        data = await resp.json()
                        if data.get('status') == 'success':
                            cursor.execute("UPDATE purchases SET status='paid' WHERE invoice_id=?", (invoice_id,))
                            conn.commit()
                            text = "Оплата пройшла! Твої маршрути:\n\n"
                            for r in routes.split(','):
                                name = VIDEOS['khust'][r]['name']
                                url = VIDEOS['khust'][r]['url']
                                text += f"Маршрут {name}: {url}\n"
                            await bot.send_message(user_id, text)
                            print(f"[AUTO] Sent to {user_id}")
            except Exception as e:
                print(f"[CHECK] Error: {e}")

# === ЗАПУСК ===
async def main():
    print("Бот запущен (Хуст, кнопка СТАРТ, авто-видео)...")
    await asyncio.gather(
        dp.start_polling(bot),
        check_pending_payments()
    )

if __name__ == '__main__':
    asyncio.run(main())
