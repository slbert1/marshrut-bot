# ВИПРАВЛЕНИЙ ТА ОПТИМІЗОВАНИЙ КОД БОТА (без помилки JSON serializable)
# Усі баги виправлено, таймаут НЕ зберігається в state → працює з Redis

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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv
from html import escape

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

# purchases — тільки хеш + last4
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

# instructors
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

# Міграція
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

# === ДАННІ ===
VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

# === УТИЛІТИ ===
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

# === FSM ===
class Order(StatesGroup):
    waiting_card = State()
    waiting_reject_reason = State()

class Support(StatesGroup):
    waiting_message = State()

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
    args = message.text.split()
    instructor_code = None
    if len(args) > 1 and args[1].startswith("inst_"):
        instructor_code = args[1].split("_", 1)[1]
        await state.update_data(instructor_code=instructor_code)

    row = cursor.execute(
        "SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()

    welcome_text = (
        "Вітаю в **Хуст ПДР Бот**!\n\n"
        "Як працює:\n"
        "1. Обери маршрут\n"
        "2. Введи номер карти (16 цифр)\n"
        "3. Переведи гроші на карту нижче\n"
        "4. Чекай підтвердження — відео прийде миттєво!\n\n"
        f"Ціни:\n• Один маршрут — {PRICE_SINGLE} грн\n• Всі 4 — {PRICE_ALL} грн\n\n"
        f"Оплата: карта `{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`\n"
        "Питання? — кнопка \"Написати адміністратору\""
    )

    if row and row[0]:
        links = row[0].split(',')
        text = "Твої куплені маршрути:\n\n" + "\n".join(links)
        await message.answer(text, reply_markup=get_main_keyboard())
    else:
        await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

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
    user_id = callback.from_user.id

    # Антиспам: не дозволяємо нове замовлення, якщо є pending
    pending = cursor.execute(
        "SELECT 1 FROM purchases WHERE user_id=? AND status='pending'", (user_id,)
    ).fetchone()
    if pending:
        await callback.answer("У тебе вже є активне замовлення! Зачекай.", show_alert=True)
        return

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

    # ТАЙМАУТ БЕЗ ЗБЕРЕЖЕННЯ В STATE
    asyncio.create_task(timeout_order(state, user_id))

    await callback.message.edit_text(
        f"Введи номер карти (16 цифр):\n"
        f"`4441111111111111`\n"
        f"Спишеться **{amount} грн**\n\n"
        f"Час на оплату: 10 хвилин",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    await state.set_state(Order.waiting_card)

async def timeout_order(state: FSMContext, user_id: int):
    await asyncio.sleep(600)  # 10 хвилин
    current_state = await state.get_state()
    if current_state == Order.waiting_card:
        await state.clear()
        try:
            await bot.send_message(user_id, "Час вийшов. Почни заново: /start")
        except:
            pass

@dp.message(Order.waiting_card)
async def get_card(message: types.Message, state: FSMContext):
    text = None
    if message.text:
        text = message.text.strip()
    elif message.caption:
        text = message.caption.strip()
    else:
        await message.answer("Надішли номер карти як текст (16 цифр).")
        return

    card = ''.join(filter(str.isdigit, text))
    if len(card) != 16:
        await message.answer("Невірно! Введи тільки 16 цифр.", reply_markup=get_back_keyboard())
        return

    if not luhn_check(card):
        await message.answer("Невірний номер картки (не пройшов Luhn). Спробуй ще.", reply_markup=get_back_keyboard())
        return

    last4 = card[-4:]
    card_hash = hash_card(card)
    formatted_display = format_card_display(last4)

    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']
    order_time = datetime.fromisoformat(data['order_time']).strftime('%H:%M:%S')
    instructor_code = data.get('instructor_code')
    username = message.from_user.username or "N/A"
    username_display = f"@{username}" if username != "N/A" else "Без username"

    # Зберігаємо
    cursor.execute(
        """INSERT INTO purchases 
        (user_id, username, card_hash, card_last4, amount, routes, status, order_time, instructor_code) 
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (message.from_user.id, username, card_hash, last4, amount, routes, order_time, instructor_code)
    )
    conn.commit()

    log.info(f"Нове замовлення: user={message.from_user.id}, amount={amount}, routes={routes}")

    await message.answer(
        f"Оплата: **{amount} грн**\n"
        f"Твоя карта: `{formatted_display}`\n"
        f"Переведи на:\n"
        f"`{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`\n\n"
        f"Чекай підтвердження...",
        parse_mode="Markdown"
    )

    routes_text = ", ".join([r.split('_')[1].upper() for r in routes.split(',')])

    admin_text = (
        f"Новий заказ!\n\n"
        f"Користувач: {escape(username_display)}\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Карта: <code>{formatted_display}</code>\n"
        f"Сума: <b>{amount} грн</b>\n"
        f"Маршрути: {escape(routes_text)}\n"
        f"Час: {order_time}"
    )
    if instructor_code:
        admin_text += f"\nІнструктор: <code>{instructor_code}</code>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{message.from_user.id}_{amount}")],
        [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{message.from_user.id}_{amount}")],
    ])

    try:
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        log.error(f"Не вдалося надіслати адміну: {e}")

    await state.clear()

# === ІНСТРУКТОР ===
@dp.message(Command("add_instructor"))
async def add_instructor(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Тільки адмін.")
        return
    args = message.text.split()
    if len(args) != 4:
        await message.answer("Використання: `/add_instructor 00001 @username 1111222233334444`", parse_mode="Markdown")
        return
    code, username, card = args[1], args[2].lstrip('@'), args[3]
    if not (code.isdigit() and len(code) == 5):
        await message.answer("Код: 5 цифр")
        return
    if not (card.isdigit() and len(card) == 16):
        await message.answer("Карта: 16 цифр")
        return

    last4 = card[-4:]
    cursor.execute(
        "INSERT OR REPLACE INTO instructors (code, username, card, card_last4) VALUES (?, ?, ?, ?)",
        (code, username, card, last4)
    )
    conn.commit()

    ref_link = f"t.me/ExamenPdr_bot?start=inst_{code}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(ref_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)

    await message.answer_photo(
        photo=bio,
        caption=(
            f"**Інструктор додано!**\n\n"
            f"Код: `{code}`\n"
            f"Username: @{username}\n"
            f"Карта: `{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}`\n\n"
            f"Посилання: {ref_link}\n"
            f"QR-код готовий!"
        ),
        parse_mode="Markdown"
    )

# === ВІДМОВА ===
@dp.callback_query(F.data.startswith("reject_"))
async def reject_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return
    try:
        _, user_id, amount = callback.data.split("_", 2)
        user_id, amount = int(user_id), int(amount)
    except:
        await callback.answer("Помилка.")
        return
    await state.update_data(reject_user_id=user_id, reject_amount=amount)
    await state.set_state(Order.waiting_reject_reason)
    await callback.message.edit_text(f"{escape(callback.message.html_text)}\n\nВведіть причину:", parse_mode="HTML")

@dp.message(Order.waiting_reject_reason)
async def reject_with_reason(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Причина коротка.")
        return
    data = await state.get_data()
    user_id = data['reject_user_id']
    amount = data['reject_amount']

    cursor.execute("UPDATE purchases SET status='rejected' WHERE user_id=? AND amount=? AND status='pending'", (user_id, amount))
    if cursor.rowcount == 0:
        await message.answer("Вже оброблено.")
        await state.clear()
        return
    conn.commit()

    try:
        await bot.send_message(user_id, f"Вибачте, {reason}.\nЗв'яжіться з адміном.", reply_markup=get_contact_admin_keyboard())
    except:
        pass
    await message.answer(f"Відмову відправлено: {reason}")
    await state.clear()

# === ПІДТРИМКА ===
@dp.callback_query(F.data == "contact_admin")
async def contact_admin(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(Support.waiting_message)
    await callback.message.answer("Напишіть повідомлення:")
    await callback.answer()

@dp.message(Support.waiting_message, F.text)
async def forward_to_admin(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    username_display = f"@{username}" if username != "N/A" else "Без username"
    row = cursor.execute("SELECT routes FROM purchases WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    route = "невідомо"
    if row:
        route = ", ".join([r.split('_')[1].upper() for r in row[0].split(',')])
    admin_text = (
        f"Повідомлення!\n\n"
        f"Користувач: {escape(username_display)}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Маршрут: {escape(route)}\n\n"
        f"Текст:\n{escape(message.text)}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрити", callback_data=f"close_dispute_{user_id}")]
    ])
    try:
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=keyboard, parse_mode="HTML")
        await message.answer("Надіслано. Очікуйте.")
        await state.clear()
    except Exception as e:
        await message.answer("Помилка.")
        log.error(f"Не вдалося: {e}")

@dp.callback_query(F.data.startswith("close_dispute_"))
async def close_dispute(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return
    try:
        user_id = int(callback.data.split("_")[-1])
    except:
        await callback.answer("Помилка.")
        return
    await callback.message.edit_text(f"{escape(callback.message.html_text)}\n\n<b>Закрито.</b>", parse_mode="HTML")
    await callback.answer("Закрито!")
    try:
        await bot.send_message(user_id, "Спор закрито. Дякуємо!")
    except:
        pass

# === СХВАЛЕННЯ ===
@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return
    try:
        _, user_id, amount = callback.data.split("_", 2)
        user_id, amount = int(user_id), int(amount)
    except:
        await callback.answer("Помилка.")
        return

    row = cursor.execute(
        "SELECT routes, instructor_code FROM purchases WHERE user_id=? AND amount=? AND status='pending'",
        (user_id, amount)
    ).fetchone()
    if not row:
        await callback.answer("Вже оброблено.")
        return

    routes, instructor_code = row
    links = [VIDEOS[r] for r in routes.split(',')]
    links_text = "\n".join(links)
    links_csv = ",".join(links)

    cursor.execute(
        "UPDATE purchases SET status='success', links=? WHERE user_id=? AND amount=? AND status='pending'",
        (links_csv, user_id, amount)
    )
    if cursor.rowcount == 0:
        await callback.answer("Не вдалося.")
        return
    conn.commit()

    await send_videos(user_id, links_text)
    await callback.message.edit_text(f"{escape(callback.message.html_text)}\n\n<b>Одобрено!</b>", parse_mode="HTML")

    try:
        await bot.send_message(user_id, "Оплата підтверджена! Відео надіслано.")
    except:
        pass

    if instructor_code:
        payout = amount * 0.1
        cursor.execute("UPDATE instructors SET total_earned = total_earned + ? WHERE code=?", (payout, instructor_code))
        conn.commit()

        inst_row = cursor.execute("SELECT username, total_earned, card_last4 FROM instructors WHERE code=?", (instructor_code,)).fetchone()
        if inst_row and inst_row[1] >= 100:
            username, earned, last4 = inst_row
            display_card = format_card_display(last4)
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"<b>{escape(username)} — виплата!</b>\n"
                    f"Накопичено: <b>{earned} грн</b>\n"
                    f"Карта: <code>{display_card}</code>\n"
                    f"Виплатіть і скиньте!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"Виплатити {earned} грн", callback_data=f"pay_{instructor_code}")]
                    ]),
                    parse_mode="HTML"
                )
            except:
                pass

async def send_videos(user_id: int, links_text: str):
    try:
        await bot.send_message(user_id, "Оплата підтверджена!\nТвої маршрути:\n\n" + links_text)
        await bot.send_message(user_id, "Обери ще:", reply_markup=get_main_keyboard())
    except Exception as e:
        log.warning(f"Не вдалося надіслати {user_id}: {e}")

# === ВИПЛАТА ===
@dp.callback_query(F.data.startswith("pay_"))
async def pay_instructor(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return
    code = callback.data.split("_")[1]
    row = cursor.execute("SELECT username, total_earned, card_last4 FROM instructors WHERE code=?", (code,)).fetchone()
    if not row:
        await callback.answer("Не знайдено.")
        return
    username, earned, last4 = row
    if earned < 100:
        await callback.answer("Менше 100 грн.")
        return

    cursor.execute("UPDATE instructors SET total_earned = 0 WHERE code=?", (code,))
    conn.commit()

    display_card = format_card_display(last4)
    await callback.message.edit_text(
        f"<b>{escape(username)} — виплата!</b>\n"
        f"Виплачено: <b>{earned} грн</b>\n"
        f"Карта: <code>{display_card}</code>\n"
        f"Скинуто.",
        parse_mode="HTML"
    )
    await callback.answer("Готово!")

# === ДАШБОРД ===
@dp.message(Command("payouts"))
async def show_payouts(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Тільки адмін.")
        return
    rows = cursor.execute("SELECT code, username, card_last4, total_earned FROM instructors WHERE total_earned > 0").fetchall()
    if not rows:
        await message.answer("Немає виплат.")
        return
    text = "<b>Виплати:</b>\n\n"
    keyboard = []
    total = 0
    for code, username, last4, earned in rows:
        username_display = f"@{username}" if username else "Без username"
        display_card = format_card_display(last4)
        status = "Готово!" if earned >= 100 else f"{earned} грн"
        text += f"{escape(username_display)} — <code>{display_card}</code> — <b>{status}</b>\n"
        if earned >= 100:
            keyboard.append([InlineKeyboardButton(text=f"Виплатити {earned} грн", callback_data=f"pay_{code}")])
        total += earned
    text += f"\n<b>Всього:</b> <b>{total} грн</b>"
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None, parse_mode="HTML")

# === СТАТИСТИКА ===
@dp.message(Command("stats"))
async def stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Тільки адмін.")
        return
    total = cursor.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    success = cursor.execute("SELECT COUNT(*) FROM purchases WHERE status='success'").fetchone()[0]
    revenue = cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM purchases WHERE status='success'").fetchone()[0]
    await message.answer(
        f"<b>Статистика:</b>\n\n"
        f"Замовлень: {total}\n"
        f"Успішних: {success}\n"
        f"Дохід: <b>{revenue} грн</b>",
        parse_mode="HTML"
    )

# === МОЇ ПОКУПКИ ===
@dp.message(Command("my"))
async def my_purchases(message: types.Message):
    row = cursor.execute(
        "SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1",
        (message.from_user.id,)
    ).fetchone()
    if row and row[0]:
        await message.answer("Твої маршрути:\n\n" + row[0].replace(',', '\n'))
    else:
        await message.answer("Нічого не куплено. /start")

# === ЗАПУСК ===
async def main():
    log.info("Бот запускається...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
