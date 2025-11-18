# ХУСТ ПДР БОТ — ФІНАЛЬНА ВЕРСІЯ (з /stats_full, /cleanup, /cancel_all)
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from io import BytesIO
import qrcode
import hashlib
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from html import escape

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE'))
PRICE_ALL = int(os.getenv('PRICE_ALL'))
ADMIN_CARD = os.getenv('ADMIN_CARD')

if not all([BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL, ADMIN_CARD]):
    raise ValueError("Заповни .env: BOT_TOKEN, ADMIN_ID, PRICE_SINGLE, PRICE_ALL, ADMIN_CARD")

bot = Bot(token=BOT_TOKEN)

# Redis або Memory
try:
    import redis.asyncio as redis
    redis_client = redis.from_url(os.getenv("REDIS_URL"))
    from aiogram.fsm.storage.redis import RedisStorage
    storage = RedisStorage(redis_client)
    log.info("Redis підключено")
except Exception as e:
    log.warning(f"Redis недоступний: {e}")
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()

dp = Dispatcher(storage=storage)

# База даних
DB_PATH = '/data/purchases.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT,
    card_hash TEXT,
    card_last4 TEXT,
    amount INTEGER,
    routes TEXT,
    status TEXT,
    order_time TEXT,
    links TEXT,
    instructor_code TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS instructors (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE,
    username TEXT,
    card TEXT,
    card_last4 TEXT,
    total_earned REAL DEFAULT 0
)
''')

# Міграція колонок
for table, col, col_type in [
    ("purchases", "links", "TEXT"),
    ("purchases", "instructor_code", "TEXT"),
    ("purchases", "card_hash", "TEXT"),
    ("purchases", "card_last4", "TEXT"),
    ("instructors", "card_last4", "TEXT")
]:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError:
        pass
conn.commit()

# Відео
VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

# Утиліти
def luhn_check(card_number: str) -> bool:
    digits = [int(d) for d in card_number]
    odd = digits[-1::-2]
    even = digits[-2::-2]
    total = sum(odd)
    for d in even:
        total += sum(divmod(d * 2, 10))
    return total % 10 == 0

def hash_card(card: str) -> str:
    return hashlib.sha256(card.encode()).hexdigest()

def format_card_display(last4: str) -> str:
    return f"**** **** **** {last4}"

# FSM
class Order(StatesGroup):
    waiting_card = State()
    waiting_reject_reason = State()

class Support(StatesGroup):
    waiting_message = State()

# Клавіатури
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_khust_all")],
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]])

def get_contact_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написати адміністратору", callback_data="contact_admin")]])

# === ХЕНДЛЕРИ ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split()
    instructor_code = None
    if len(args) > 1 and args[1].startswith("inst_"):
        instructor_code = args[1].split("_", 1)[1]
        await state.update_data(instructor_code=instructor_code)

    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()

    welcome_text = (
        "Вітаю в **Хуст ПДР Бот**!\n\n"
        "Як працює:\n"
        "1. Обери маршрут\n"
        "2. Введи номер карти (16 цифр)\n"
        "3. Переведи гроші\n"
        "4. Чекай — відео прийде миттєво!\n\n"
        f"Ціни:\n• Один маршрут — {PRICE_SINGLE} грн\n• Всі 4 — {PRICE_ALL} грн\n\n"
        f"Карта для оплати: `{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`"
    )

    if row and row[0]:
        await message.answer("Твої куплені маршрути:\n\n" + row[0].replace(',', '\n'), reply_markup=get_main_keyboard())
    else:
        await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Обери маршрут:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data.startswith("buy_"))
async def handle_purchase(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    pending = cursor.execute("SELECT 1 FROM purchases WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
    if pending:
        await callback.answer("У тебе вже є активне замовлення! Зачекай.", show_alert=True)
        return

    routes_map = {
        "buy_khust_route1": ("khust_route1", PRICE_SINGLE),
        "buy_khust_route8": ("khust_route8", PRICE_SINGLE),
        "buy_khust_route6": ("khust_route6", PRICE_SINGLE),
        "buy_khust_route2": ("khust_route2", PRICE_SINGLE),
        "buy_khust_all": (",".join(VIDEOS.keys()), PRICE_ALL),
    }

    routes, amount = routes_map[callback.data]

    await state.update_data(amount=amount, routes=routes, order_time=datetime.now().isoformat())
    asyncio.create_task(timeout_order(state, user_id))

    await callback.message.edit_text(
        f"Введи номер карти (16 цифр):\n"
        f"`4441111111111111`\n"
        f"Сума: **{amount} грн**\n\n"
        f"Час на оплату: 10 хвилин",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    await state.set_state(Order.waiting_card)

async def timeout_order(state: FSMContext, user_id: int):
    await asyncio.sleep(600)
    if await state.get_state() == Order.waiting_card:
        await state.clear()
        try:
            await bot.send_message(user_id, "Час вийшов. Почни заново: /start")
        except:
            pass

@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    card = ''.join(filter(str.isdigit, message.text or ""))
    if len(card) != 16 or not luhn_check(card):
        await message.answer("Невірний номер! Тільки 16 цифр.", reply_markup=get_back_keyboard())
        return

    last4 = card[-4:]
    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']
    instructor_code = data.get('instructor_code')

    cursor.execute("""
        INSERT INTO purchases 
        (user_id, username, card_hash, card_last4, amount, routes, status, order_time, instructor_code)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (message.from_user.id, message.from_user.username or "N/A",
          hash_card(card), last4, amount, routes,
          datetime.now().strftime('%H:%M'), instructor_code))
    conn.commit()

    await message.answer(
        f"Очікуємо оплату {amount} грн на карту:\n`{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`\n\nЧекай підтвердження...",
        parse_mode="Markdown"
    )

    routes_text = ", ".join([r.split('_')[1].upper() for r in routes.split(',')])
    admin_text = (
        f"Новий заказ!\n\n"
        f"Користувач: {escape(message.from_user.username or 'N/A')}\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Карта: <code>**** **** **** {last4}</code>\n"
        f"Сума: <b>{amount} грн</b>\n"
        f"Маршрути: {routes_text}"
    )
    if instructor_code:
        admin_text += f"\nІнструктор: <code>{instructor_code}</code>"

    await bot.send_message(
        ADMIN_ID,
        admin_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{message.from_user.id}_{amount}")],
            [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{message.from_user.id}_{amount}")]
        ]),
        parse_mode="HTML"
    )
    await state.clear()

# === КОМАНДИ АДМІНА ===
@dp.message(Command("stats"))
async def stats(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    total = cursor.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    success = cursor.execute("SELECT COUNT(*) FROM purchases WHERE status='success'").fetchone()[0]
    revenue = cursor.execute("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='success'").fetchone()[0]
    await message.answer(f"<b>Статистика:</b>\nЗамовлень: {total}\nУспішних: {success}\nДохід: <b>{revenue} грн</b>", parse_mode="HTML")

@dp.message(Command("stats_full"))
async def stats_full(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    rows = cursor.execute("SELECT status, COUNT(*), COALESCE(SUM(amount),0) FROM purchases GROUP BY status").fetchall()
    text = "<b>Повна статистика:</b>\n\n"
    real = 0
    for status, cnt, suma in rows:
        emoji = {"success": "Успішно", "pending": "Очікують", "cancelled": "Скасовано", "rejected": "Відмовлено"}.get(status, status)
        text += f"{emoji} <b>{status}</b>: {cnt} → {suma} грн\n"
        if status == "success": real = suma
    text += f"\n<b>Реальний дохід:</b> {real} грн"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("cancel_all"))
async def cancel_all_pending(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    cancelled = cursor.execute("UPDATE purchases SET status='cancelled' WHERE status='pending'").rowcount
    conn.commit()
    await message.answer(f"Скасовано {cancelled} активних замовлень!")

@dp.message(Command("cleanup"))
async def cleanup_db(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    deleted = cursor.execute("DELETE FROM purchases WHERE status IN ('cancelled', 'pending')").rowcount
    conn.commit()
    await message.answer(f"Видалено {deleted} тестових записів!\nСтатистика тепер чиста!")

@dp.message(Command("my"))
async def my_purchases(message: types.Message):
    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (message.from_user.id,)).fetchone()
    if row and row[0]:
        await message.answer("Твої маршрути:\n\n" + row[0].replace(',', '\n'))
    else:
        await message.answer("Нічого не куплено. /start")

# === ІНШІ ХЕНДЛЕРИ (approve, reject, інструктори, виплати тощо) — залишив без змін для компактності ===
# (якщо потрібно — додам повністю, але вони в тебе вже є і працюють)

async def main():
    log.info("Бот запущено!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
