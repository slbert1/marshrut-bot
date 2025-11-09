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

# === ВИДЕО ПО ГОРОДАМ ===
VIDEOS = {
    'khust': {
        'route1': {'name': '№1', 'url': 'https://youtu.be/mxtsqKmXWSI'},
        'route8': {'name': '№8', 'url': 'https://youtu.be/7VwtAAaQWE8'},
        'route6': {'name': '№6', 'url': 'https://youtu.be/RnpOEKIddZw'},
        'route2': {'name': '№2', 'url': 'https://youtu.be/RllCGT6dOPc'},
    },
    # Добавь другие города по аналогии
}

class Cart(StatesGroup):
    choosing_city = State()
    viewing_routes = State()
    paying = State()

# === СТАРТ ===
@dp.message(Command('start'))
async def start(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith('paid_'):
        await manual_paid(message)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Хуст", callback_data="city_khust")],
        [InlineKeyboardButton(text="Київ", callback_data="city_kyiv")],
        [InlineKeyboardButton(text="Львів", callback_data="city_lviv")],
        [InlineKeyboardButton(text="Одеса", callback_data="city_odesa")],
    ])
    await message.answer(
        "Обери місто:",
        reply_markup=kb
    )
    await state.set_state(Cart.choosing_city)

# === ВЫБОР ГОРОДА ===
@dp.callback_query(F.data.startswith("city_"))
async def choose_city(callback: types.CallbackQuery, state: FSMContext):
    city = callback.data.split("_")[1]
    await state.update_data(city=city)

    if city not in VIDEOS:
        await callback.message.edit_text("Маршрути для цього міста ще не додані.")
        return

    routes = VIDEOS[city]
    kb = []
    for key, video in routes.items():
        kb.append([InlineKeyboardButton(text=f"Маршрут {video['name']} — 250 грн", callback_data=f"buy_{city}_{key}")])
    kb.append([InlineKeyboardButton(text="Всі маршрути — 1000 грн", callback_data=f"buy_{city}_all")])
    kb.append([InlineKeyboardButton(text="Назад до міст", callback_data="back_to_cities")])

    await callback.message.edit_text(
        f"Маршрути — {city.title()}\n\nОбери:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.set_state(Cart.viewing_routes)

# === НАЗАД К ГОРОДАМ ===
@dp.callback_query(F.data == "back_to_cities")
async def back_to_cities(callback: types.CallbackQuery, state: FSMContext):
    await start(callback.message, state)

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_"))
async def buy(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data.split("_")
    city = data[1]
    route_key = data[2] if len(data) > 2 else None

    user_data = await state.get_data()
    if user_data.get('city') != city:
        await callback.answer("Помилка. Почни заново: /start")
        return

    amount = 1000 if route_key == "all" else 250
    desc = f"Всі маршрути — {city.title()}" if route_key == "all" else f"Маршрут {VIDEOS[city][route_key]['name']} — {city.title()}"
    routes = ",".join(VIDEOS[city].keys()) if route_key == "all" else route_key

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

# === MONO ===
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

# === РУЧНАЯ ПРОВЕРКА ===
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
        city_routes = row[0].split(',')
        # Определяем город по первому маршруту
        city = next((c for c, routes in VIDEOS.items() if city_routes[0] in routes), 'khust')
        for r in city_routes:
            name = VIDEOS[city][r]['name']
            url = VIDEOS[city][r]['url']
            text += f"Маршрут {name}: {url}\n"
        await message.answer(text)
    else:
        await message.answer("Оплата ще обробляється. Зачекай 1-2 хв.")

# === АВТО-ПРОВЕРКА ===
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
                            city = next((c for c, r in VIDEOS.items() if routes.split(',')[0] in r), 'khust')
                            text = "Оплата пройшла! Твої маршрути:\n\n"
                            for r in routes.split(','):
                                name = VIDEOS[city][r]['name']
                                url = VIDEOS[city][r]['url']
                                text += f"Маршрут {name}: {url}\n"
                            await bot.send_message(user_id, text)
                            print(f"[AUTO] Sent to {user_id}")
            except Exception as e:
                print(f"[CHECK] Error: {e}")

async def main():
    print("Бот запущен...")
    await asyncio.gather(
        dp.start_polling(bot),
        check_pending_payments()
    )

if __name__ == '__main__':
    asyncio.run(main())
