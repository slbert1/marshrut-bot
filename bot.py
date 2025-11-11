import os
import asyncio
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE'))
PRICE_ALL = int(os.getenv('PRICE_ALL'))

if not all([BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL]):
    raise ValueError("BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL — обязательны!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

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

VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

class Order(StatesGroup):
    waiting_card = State()

def get_routes_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_khust_all")],
    ])

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        f"Обери маршрут:\n\n"
        f"Кожен — {PRICE_SINGLE} грн\n"
        f"Всі 4 — {PRICE_ALL} грн\n\n"
        f"Оплата на карту — відео миттєво!",
        reply_markup=get_routes_keyboard()
    )

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
        f"Введи номер карти (16 цифр):\n"
        f"`4441111111111111`\n"
        f"Спишеться **{amount} грн**",
        parse_mode="Markdown"
    )
    await state.set_state(Order.waiting_card)

@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    raw_input = message.text.strip()
    card = ''.join(filter(str.isdigit, raw_input))
    if len(card) != 16:
        await message.answer("Невірно! Введи 16 цифр.")
        return

    formatted_card = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']
    order_time = datetime.now().strftime('%H:%M:%S')

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
        f"`4441 1144 6012 6863`\n"
        f"Іжганайтіс Альберт\n\n"
        f"Чекай підтвердження...",
        parse_mode="Markdown"
    )

    routes_text = ", ".join([r.split('_')[1].upper() for r in routes.split(',')])
    admin_text = (
        f"Новий заказ!\n\n"
        f"Користувач: @{message.from_user.username or 'N/A'}\n"
        f"ID: `{message.from_user.id}`\n"
        f"Карта: `{formatted_card}`\n"
        f"Сума: **{amount} грн**\n"
        f"Маршрути: {routes_text}\n"
        f"Час: {order_time}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{message.from_user.id}_{amount}")]
    ])
    await bot.send_message(ADMIN_ID, admin_text, reply_markup=keyboard, parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return

    _, user_id, amount = callback.data.split("_")
    user_id = int(user_id)
    amount = int(amount)

    row = cursor.execute(
        "SELECT routes FROM purchases WHERE user_id=? AND amount=? AND status='pending'",
        (user_id, amount)
    ).fetchone()

    if not row:
        await callback.answer("Замовлення не знайдено.")
        return

    routes = row[0]
    await send_videos(user_id, routes)
    cursor.execute("UPDATE purchases SET status='success' WHERE user_id=? AND amount=?", (user_id, amount))
    conn.commit()

    await callback.message.edit_text(f"{callback.message.text}\n\nОдобрено!", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, "Оплата підтверджена! Відео надіслано.")
    except:
        pass

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

async def main():
    print("Бот запущен! Ручной режим.")
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            print(f"[ОШИБКА] {e} — переподключение через 5 сек...")
            await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(main())
