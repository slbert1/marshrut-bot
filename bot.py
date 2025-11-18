# ХУСТ ПДР БОТ — ВЕРСІЯ 2.0 (тільки останні 4 цифри карти)
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
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
    raise ValueError("Заповни .env!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect('/data/purchases.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, card_last4 TEXT,
    amount INTEGER, routes TEXT, status TEXT, order_time TEXT, links TEXT, instructor_code TEXT)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS instructors (
    id INTEGER PRIMARY KEY, code TEXT UNIQUE, username TEXT, card TEXT, total_earned REAL DEFAULT 0)''')

for col in ["links", "instructor_code", "card_last4"]:
    try: cursor.execute(f"ALTER TABLE purchases ADD COLUMN {col} TEXT")
    except: pass
conn.commit()

VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

class Order(StatesGroup):
    waiting_last4 = State()
    waiting_reject_reason = State()

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_all")],
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]])

def get_contact_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написати адміну", callback_data="contact_admin")]])

# === START ===
@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("inst_"):
        await state.update_data(instructor_code=args[1].split("_", 1)[1])

    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (m.from_user.id,)).fetchone()
    if row and row[0]:
        await m.answer("Твої маршрути:\n\n" + row[0].replace(',', '\n'), reply_markup=get_main_keyboard())
    else:
        await m.answer(
            f"Вітаю в Хуст ПДР Бот!\n\n"
            f"Обери маршрут → введи останні 4 цифри карти → отримай відео миттєво!\n\n"
            f"Ціна: {PRICE_SINGLE} грн за маршрут │ {PRICE_ALL} грн за всі 4\n\n"
            f"Карта для оплати:\n`{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`",
            reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_menu")
async def back(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Обери маршрут:", reply_markup=get_main_keyboard())

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_"))
async def buy_route(c: types.CallbackQuery, state: FSMContext):
    if cursor.execute("SELECT 1 FROM purchases WHERE user_id=? AND status='pending'", (c.from_user.id,)).fetchone():
        await c.answer("У тебе вже є активне замовлення!", show_alert=True)
        return

    routes_map = {
        "buy_route1": ("khust_route1", PRICE_SINGLE),
        "buy_route8": ("khust_route8", PRICE_SINGLE),
        "buy_route6": ("khust_route6", PRICE_SINGLE),
        "buy_route2": ("khust_route2", PRICE_SINGLE),
        "buy_all": (",".join(VIDEOS.keys()), PRICE_ALL),
    }
    routes, amount = routes_map[c.data]

    await state.update_data(amount=amount, routes=routes)
    await c.message.edit_text(
        f"Введи **останні 4 цифри карти**, з якої оплатив:\n\n"
        f"Наприклад: `1234`\n"
        f"Сума: **{amount} грн**",
        parse_mode="Markdown", reply_markup=get_back_keyboard())
    await state.set_state(Order.waiting_last4)

@dp.message(Order.waiting_last4)
async def get_last4(m: types.Message, state: FSMContext):
    last4 = ''.join(filter(str.isdigit, m.text or ""))
    if len(last4) != 4:
        await m.answer("Помилка! Введи рівно 4 цифри.", reply_markup=get_back_keyboard())
        return

    data = await state.get_data()
    amount = data['amount']
    routes = data['routes']
    instructor_code = data.get('instructor_code')

    cursor.execute("""INSERT INTO purchases 
        (user_id, username, card_last4, amount, routes, status, order_time, instructor_code)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (m.from_user.id, m.from_user.username or "N/A", last4, amount, routes,
         datetime.now().strftime("%H:%M"), instructor_code))
    conn.commit()

    await m.answer("Дякую! Чекай підтвердження від адміністратора...")

    routes_text = ", ".join(r.split("_")[1].upper() for r in routes.split(","))
    admin_text = (f"Новий заказ!\n\n"
                  f"Користувач: @{escape(m.from_user.username or 'N/A')}\n"
                  f"ID: <code>{m.from_user.id}</code>\n"
                  f"Останні цифри карти: <b>{last4}</b>\n"
                  f"Сума: <b>{amount} грн</b>\n"
                  f"Маршрути: {routes_text}")
    if instructor_code:
        admin_text += f"\nРеферал: <code>{instructor_code}</code>"

    await bot.send_message(ADMIN_ID, admin_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{m.from_user.id}_{amount}")],
            [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{m.from_user.id}_{amount}")]
        ]), parse_mode="HTML")
    await state.clear()

# === ОДОБРИТИ / ВІДМОВИТИ — без змін (працюють як раніше) ===
@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return await c.answer("Ти не адмін!", show_alert=True)
    try:
        _, uid, amt = c.data.split("_")
        uid, amt = int(uid), int(amt)
    except: return await c.answer("Помилка")

    row = cursor.execute("SELECT routes, instructor_code FROM purchases WHERE user_id=? AND amount=? AND status='pending'", (uid, amt)).fetchone()
    if not row: return await c.answer("Вже оброблено")

    routes, inst_code = row
    links = [VIDEOS[r.strip()] for r in routes.split(",") if r.strip() in VIDEOS]
    links_text = "\n".join(links)
    links_csv = ",".join(links)

    cursor.execute("UPDATE purchases SET status='success', links=? WHERE user_id=? AND amount=?", (links_csv, uid, amt))
    conn.commit()

    await bot.send_message(uid, "Оплата підтверджена!\nТвої відео:\n\n" + links_text)
    await bot.send_message(uid, "Можеш купити ще:", reply_markup=get_main_keyboard())
    await c.message.edit_text(c.message.html_text + "\n\nОдобрено та видано!", parse_mode="HTML")

    if inst_code:
        cursor.execute("UPDATE instructors SET total_earned = total_earned + ? WHERE code=?", (amt * 0.1, inst_code))
        conn.commit()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_init(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    try:
        _, uid, amt = c.data.split("_")
        uid, amt = int(uid), int(amt)
    except: return
    await state.update_data(rej_uid=uid, rej_amt=amt)
    await state.set_state(Order.waiting_reject_reason)
    await c.message.edit_text(c.message.html_text + "\n\nВведи причину відмови:", parse_mode="HTML")

@dp.message(Order.waiting_reject_reason)
async def reject_reason(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    cursor.execute("UPDATE purchases SET status='rejected' WHERE user_id=? AND amount=? AND status='pending'",
                   (data['rej_uid'], data['rej_amt']))
    conn.commit()
    await bot.send_message(data['rej_uid'], f"Оплата не підтверджена.\nПричина: {m.text}\nЗвернись до адміна.")
    await m.answer("Відмову відправлено")
    await state.clear()

# === АДМІНСЬКІ КОМАНДИ ===
@dp.message(Command("stats_full"))
async def stats_full(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    rows = cursor.execute("SELECT status, COUNT(*), COALESCE(SUM(amount),0) FROM purchases GROUP BY status").fetchall()
    text = "<b>Повна статистика:</b>\n\n"
    real = 0
    for s, cnt, suma in rows:
        emoji = {"success":"Успішно","pending":"Очікують","cancelled":"Скасовано","rejected":"Відмовлено"}.get(s,s)
        text += f"{emoji} <b>{s}</b>: {cnt} → {suma} грн\n"
        if s == "success": real = suma
    text += f"\n<b>Реальний дохід:</b> {real} грн"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("cancel_all"))
async def cancel_all(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    c = cursor.execute("UPDATE purchases SET status='cancelled' WHERE status='pending'").rowcount
    conn.commit()
    await m.answer(f"Скасовано {c} замовлень")

@dp.message(Command("cleanup"))
async def cleanup(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    d = cursor.execute("DELETE FROM purchases WHERE status IN ('cancelled','pending')").rowcount
    conn.commit()
    await m.answer(f"Видалено {d} тестових записів!")

@dp.message(Command("my"))
async def my(m: types.Message):
    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (m.from_user.id,)).fetchone()
    if row and row[0]:
        await m.answer("Твої маршрути:\n\n" + row[0].replace(',','\n'))
    else:
        await m.answer("Нічого не куплено")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
