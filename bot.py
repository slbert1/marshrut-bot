import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
load_dotenv()

# === КОНФИГ ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE'))
PRICE_ALL = int(os.getenv('PRICE_ALL'))
ADMIN_CARD = os.getenv('ADMIN_CARD')

if not all([BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL, ADMIN_CARD]):
    raise ValueError("Заповни .env: BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL, ADMIN_CARD")

bot = Bot(token=BOT_TOKEN)

# === Redis для FSM ===
try:
    import redis.asyncio as redis
    redis_client = redis.from_url(os.getenv("REDIS_URL"))
    from aiogram.fsm.storage.redis import RedisStorage
    storage = RedisStorage(redis_client)
    log.info("Redis підключено — стани не втрачаються!")
except Exception as e:
    log.warning(f"Redis недоступний: {e}. Використовуємо MemoryStorage (тільки для тесту!)")
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()

dp = Dispatcher(storage=storage)

# === БД ===
DB_PATH = '/data/purchases.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    order_time TEXT,
    links TEXT
)
''')

try:
    cursor.execute("ALTER TABLE purchases ADD COLUMN links TEXT")
    log.info("Столбець 'links' додано в БД!")
except sqlite3.OperationalError:
    pass

conn.commit()

# === ДАННІ ===
VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

# === FSM ===
class Order(StatesGroup):
    waiting_card = State()
    waiting_reject_reason = State()

class Support(StatesGroup):
    waiting_message = State()  # ← НОВОЕ СОСТОЯНИЕ

# === КЛАВИАТУРЫ ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_khust_all")],
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]
    ])

def get_contact_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написати адміністратору", callback_data="contact_admin")]
    ])

# === ХЕНДЛЕРИ ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    row = cursor.execute(
        "SELECT links FROM purchases WHERE user_id=? AND status='success'",
        (user_id,)
    ).fetchone()
    if row and row[0]:
        links = row[0].split(',')
        text = "Твої куплені маршрути:\n\n" + "\n".join(links)
        await message.answer(text, reply_markup=get_main_keyboard())
    else:
        await message.answer(
            f"Обери маршрут:\n\n"
            f"Кожен — {PRICE_SINGLE} грн\n"
            f"Всі 4 — {PRICE_ALL} грн\n\n"
            f"Оплата на карту — відео миттєво!",
            reply_markup=get_main_keyboard()
        )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f"Обери маршрут:\n\n"
        f"Кожен — {PRICE_SINGLE} грн\n"
        f"Всі 4 — {PRICE_ALL} грн\n\n"
        f"Оплата на карту — відео миттєво!",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data.startswith("buy_"))
async def handle_purchase(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data
    routes_map = {
        "buy_khust_route1": ("khust_route1", PRICE_SINGLE),
        "buy_khust_route8": ("khust_route8", PRICE_SINGLE),
        "buy_khust_route6": ("khust_route6", PRICE_SINGLE),
        "buy_khust_route2": ("khust_route2", PRICE_SINGLE),
        "buy_khust_all": (",".join(VIDEOS.keys()), PRICE_ALL),
    }
    routes, amount = routes_map[action]
    
    await state.update_data(
        amount=amount,
        routes=routes,
        order_time=datetime.now().isoformat()
    )
    
    await callback.message.edit_text(
        f"Введи номер карти (16 цифр):\n"
        f"`4441111111111111`\n"
        f"Спишеться **{amount} грн**\n\n"
        f"Час на оплату: 10 хвилин",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    await state.set_state(Order.waiting_card)
    asyncio.create_task(timeout_order(state, callback.from_user.id))

async def timeout_order(state: FSMContext, user_id: int):
    await asyncio.sleep(600)
    current_state = await state.get_state()
    if current_state == Order.waiting_card:
        await state.clear()
        try:
            await bot.send_message(user_id, "Час вийшов. Почни заново: /start")
        except:
            pass

@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    raw_input = message.text.strip()
    card = ''.join(filter(str.isdigit, raw_input))
    if not raw_input.isdigit() or len(card) != 16:
        await message.answer("Невірно! Введи тільки 16 цифр.", reply_markup=get_back_keyboard())
        return

    formatted_card = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']
    order_time = datetime.fromisoformat(data['order_time']).strftime('%H:%M:%S')

    cursor.execute(
        "INSERT INTO purchases (user_id, username, card, amount, routes, status, order_time) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (message.from_user.id, message.from_user.username or "N
