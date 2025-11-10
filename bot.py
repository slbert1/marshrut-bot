import os
import asyncio
import aiohttp
import sqlite3
import time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

# === ОБЯЗАТЕЛЬНО ДОЛЖНЫ БЫТЬ В .env ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONO_TOKEN = os.getenv('MONO_TOKEN')
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE'))   # ОБЯЗАТЕЛЬНО!
PRICE_ALL = int(os.getenv('PRICE_ALL'))         # ОБЯЗАТЕЛЬНО!

# Проверка на запуск
if not all([BOT_TOKEN, MONO_TOKEN, PRICE_SINGLE, PRICE_ALL]):
    raise ValueError("Ошибка: Заполни BOT_TOKEN, MONO_TOKEN, PRICE_SINGLE, PRICE_ALL в .env!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === БАЗА ===
conn = sqlite3.connect('purchases.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        username TEXT,
        card TEXT,
        amount INTEGER,
        routes TEXT,
        status TEXT,
        order_time TEXT
    )
''')
conn.commit()

# === ВИДЕО ===
VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

# === FSM ===
class Order(StatesGroup):
    waiting_card = State()

# === СТАРТ ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_single")],
        [InlineKeyboardButton(text=f"Всі маршрути — {PRICE_ALL} грн", callback_data="buy_all")],
    ])
    await message.answer(
        f"Обери маршрут:\n\n"
        f"1 маршрут — {PRICE_SINGLE} грн\n"
        f"Всі 4 — {PRICE_ALL} грн",
        reply_markup=kb
    )

# === ПОКУПКА ===
@dp.callback_query(F.data.in_({"buy_single", "buy_all"}))
async def buy_route(callback: types.CallbackQuery, state: FSMContext):
    amount = PRICE_SINGLE if callback.data == "buy_single" else PRICE_ALL
    routes = "khust_route1" if amount == PRICE_SINGLE else ",".join(VIDEOS.keys())

    await state.update_data(amount=amount, routes=routes)
    await callback.message.edit_text(
        f"Введи **номер своєї карти** (16 цифр, наприклад: 4441111111111111)\n"
        f"З неї спишуться {amount} грн",
        parse_mode="Markdown"
    )
    await state.set_state(Order.waiting_card)

# === КАРТА ===
@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    card = message.text.strip().replace(" ", "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("Невірний формат. Введи 16 цифр без пробілів.")
        return

    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']

    order_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO purchases (user_id, username, card, amount, routes, status, order_time) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (message.from_user.id, message.from_user.username or "N/A", card, amount, routes, order_time)
    )
    conn.commit()

    await message.answer(
        f"Оплати **{amount} грн** на карту:\n"
        f"`5168 7573 0461 7889`\n"
        f"Іжганайтіс Альберт\n\n"
        f"Після оплати — бот перевірить за 30-60 сек!",
        parse_mode="Markdown"
    )
    await state.clear()

# === ПРОВЕРКА ВЫПИСКИ ===
async def check_transactions():
    last_check = int(time.time()) - 120
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {'X-Token': MONO_TOKEN}
                async with session.get(
                    f'https://api.monobank.ua/personal/statement/0/{last_check}',
                    headers=headers
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(30)
                        continue
                    data = await resp.json()

                    for tx in data:
                        if tx.get('amount', 0) <= 0:
                            continue

                        amount_cents = tx['amount']
                        amount_uah = amount_cents // 100

                        order_time = datetime.fromtimestamp(tx['time'])
                        time_window_start = (order_time - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
                        time_window_end = (order_time + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')

                        masked_pan = tx.get('maskedPan', [None])[0]
                        if not masked_pan:
                            continue

                        row = cursor.execute(
                            "SELECT user_id, routes FROM purchases "
                            "WHERE amount=? AND status='pending' "
                            "AND order_time BETWEEN ? AND ? "
                            "AND card LIKE ?",
                            (amount_uah, time_window_start, time_window_end, f"%{masked_pan[-4:]}")
                        ).fetchone()

                        if row:
                            user_id, routes = row
                            await send_videos(user_id, routes)
                            cursor.execute("UPDATE purchases SET status='success' WHERE user_id=? AND amount=?", (user_id, amount_uah))
                            conn.commit()

                    last_check = int(time.time())
        except Exception as e:
            print(f"[CHECK ERROR]: {e}")
        await asyncio.sleep(30)

async def send_videos(user_id: int, routes: str):
    text = "Оплата підтверджена!\nТвої маршрути:\n\n"
    for r in routes.split(','):
        name = r.split('_')[1].upper()
        url = VIDEOS[r]
        text += f"Маршрут {name}: {url}\n"
    try:
        await bot.send_message(user_id, text)
    except:
        pass

# === ЗАПУСК ===
async def main():
    print(f"Бот запущен! Ціни: {PRICE_SINGLE} / {PRICE_ALL} грн")
    asyncio.create_task(check_transactions())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
