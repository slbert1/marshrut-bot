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

# === ЦЕНЫ ТОЛЬКО ИЗ RENDER Environment — БЕЗ УМОЛЧАНИЙ! ===
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE'))  # ОБЯЗАТЕЛЬНО!
PRICE_ALL = int(os.getenv('PRICE_ALL'))        # ОБЯЗАТЕЛЬНО!

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONO_TOKEN = os.getenv('MONO_TOKEN')

# === ПРОВЕРКА НАЛИЧИЯ ВСЕХ ПЕРЕМЕННЫХ ===
if not all([BOT_TOKEN, MONO_TOKEN, PRICE_SINGLE, PRICE_ALL]):
    raise ValueError("Ошибка: BOT_TOKEN, MONO_TOKEN, PRICE_SINGLE, PRICE_ALL — обязательны в Render Environment!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === БАЗА ДАННЫХ ===
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
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',  # Хуст → Івано-Франківськ
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',  # Хуст → Одеса
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',  # Хуст → Київ
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',  # Хуст → Львів
}

# === FSM ===
class Order(StatesGroup):
    waiting_card = State()

# === КЛАВИАТУРА — ЦЕНЫ ИЗ env ===
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
async def start(message: types.Message):
    await message.answer(
        f"Обери маршрут:\n\n"
        f"Кожен — {PRICE_SINGLE} грн\n"
        f"Всі 4 — {PRICE_ALL} грн\n\n"
        f"Оплата на карту — відео миттєво!",
        reply_markup=get_routes_keyboard()
    )

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_"))
async def handle_purchase(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data

    routes_map = {
        "buy_khust_route1": "khust_route1",
        "buy_khust_route8": "khust_route8",
        "buy_khust_route6": "khust_route6",
        "buy_khust_route2": "khust_route2",
        "buy_khust_all": "khust_route1,khust_route8,khust_route6,khust_route2"
    }

    routes = routes_map[action]
    amount = PRICE_ALL if action == "buy_khust_all" else PRICE_SINGLE

    await state.update_data(amount=amount, routes=routes)

    await callback.message.edit_text(
        f"Введи номер карти (16 цифр, можна без пробілів):\n"
        f"`4441111111111111` або `4441 1111 1111 1111`\n"
        f"Спишеться **{amount} грн**",
        parse_mode="Markdown"
    )
    await state.set_state(Order.waiting_card)

# === КАРТА С АВТОМАСКОЙ ===
@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    raw_input = message.text.strip()
    card = ''.join(filter(str.isdigit, raw_input))  # Убираем всё кроме цифр
    
    if len(card) != 16:
        await message.answer(
            "Невірний формат!\n"
            "Введи 16 цифр (можна без пробілів):\n"
            "`4441111111111111` або `4441 1111 1111 1111`",
            parse_mode="Markdown"
        )
        return

    # === АВТОМАТИЧЕСКАЯ МАСКА ===
    formatted_card = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    # === КОНЕЦ МАСКИ ===

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
        f"Оплата: **{amount} грн**\n"
        f"Карта: `{formatted_card}`\n"
        f"Переведи на:\n"
        f"`5168 7573 0461 7889`\n"
        f"Іжганайтіс Альберт\n\n"
        f"Через 30-60 сек — відео!",
        parse_mode="Markdown"
    )
    await state.clear()

# === ПРОВЕРКА ПЛАТЕЖЕЙ ===
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

# === ОТПРАВКА ВИДЕО ===
async def send_videos(user_id: int, routes: str):
    text = "Оплата підтверджена!\nТвої маршрути:\n\n"
    for r in routes.split(','):
        name = r.split('_')[1].upper()
        url = VIDEOS[r]
        text += f"Маршрут {name}: {url}\n"
    try:
        await bot.send_message(user_id, text)
    except:
        pass  # пользователь заблокировал бота

# === ЗАПУСК ===
async def main():
    print(f"Бот запущен! Ціни: {PRICE_SINGLE} / {PRICE_ALL} грн")
    asyncio.create_task(check_transactions())
    await dp.start_polling(bot)
# === ВЕБ-СЕРВЕР ДЛЯ RENDER (открывает порт 10000) ===
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "bot is alive"}

if __name__ == '__main__':
    # Запуск бота в фоне
    asyncio.create_task(main())
    # Запуск веб-сервера
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv('PORT', 10000)))

if __name__ == '__main__':
    asyncio.run(main())
