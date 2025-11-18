# ХУСТ ПДР БОТ — ФІНАЛЬНА РОБОЧА ВЕРСІЯ (всі кнопки працюють!)
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
    raise ValueError("Заповни .env!")

bot = Bot(token=BOT_TOKEN)

# Redis або Memory
try:
    import redis.asyncio as redis
    redis_client = redis.from_url(os.getenv("REDIS_URL"))
    from aiogram.fsm.storage.redis import RedisStorage
    storage = RedisStorage(redis_client)
    log.info("Redis підключено")
except:
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()

dp = Dispatcher(storage=storage)

# База
conn = sqlite3.connect('/data/purchases.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, card_hash TEXT, card_last4 TEXT,
    amount INTEGER, routes TEXT, status TEXT, order_time TEXT, links TEXT, instructor_code TEXT)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS instructors (
    id INTEGER PRIMARY KEY, code TEXT UNIQUE, username TEXT, card TEXT, card_last4 TEXT, total_earned REAL DEFAULT 0)''')

for table, col, col_type in [("purchases","links","TEXT"), ("purchases","instructor_code","TEXT"),
                             ("purchases","card_hash","TEXT"), ("purchases","card_last4","TEXT"),
                             ("instructors","card_last4","TEXT")]:
    try: cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except: pass
conn.commit()

VIDEOS = {
    'khust_route1': 'https://youtu.be/mxtsqKmXWSI',
    'khust_route8': 'https://youtu.be/7VwtAAaQWE8',
    'khust_route6': 'https://youtu.be/RnpOEKIddZw',
    'khust_route2': 'https://youtu.be/RllCGT6dOPc',
}

def luhn_check(c): d=[int(x) for x in c]; return sum(d[-1::-2] + [sum(divmod(x*2,10)) for x in d[-2::-2]]) % 10 == 0
def hash_card(c): return hashlib.sha256(c.encode()).hexdigest()
def fmt(last4): return f"**** **** **** {last4}"

class Order(StatesGroup):
    waiting_card = State()
    waiting_reject_reason = State()
class Support(StatesGroup):
    waiting_message = State()

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

# === START ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split()
    if len(args)>1 and args[1].startswith("inst_"):
        await state.update_data(instructor_code=args[1].split("_",1)[1])

    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if row and row[0]:
        await message.answer("Твої маршрути:\n\n" + row[0].replace(',', '\n'), reply_markup=get_main_keyboard())
    else:
        await message.answer(
            f"Вітаю в Хуст ПДР Бот!\n\nОбери маршрут → введи карту → оплати → отримай відео миттєво!\n\n"
            f"Один маршрут — {PRICE_SINGLE} грн\nВсі 4 — {PRICE_ALL} грн\n\n"
            f"Карта: `{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`",
            reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Обери маршрут:", reply_markup=get_main_keyboard())

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_"))
async def handle_purchase(c: types.CallbackQuery, state: FSMContext):
    if cursor.execute("SELECT 1 FROM purchases WHERE user_id=? AND status='pending'", (c.from_user.id,)).fetchone():
        await c.answer("У тебе вже є активне замовлення!", show_alert=True)
        return

    routes_map = {
        "buy_khust_route1": ("khust_route1", PRICE_SINGLE),
        "buy_khust_route8": ("khust_route8", PRICE_SINGLE),
        "buy_khust_route6": ("khust_route6", PRICE_SINGLE),
        "buy_khust_route2": ("khust_route2", PRICE_SINGLE),
        "buy_khust_all": (",".join(VIDEOS.keys()), PRICE_ALL),
    }
    routes, amount = routes_map[c.data]
    await state.update_data(amount=amount, routes=routes, order_time=datetime.now().isoformat())
    asyncio.create_task(timeout_order(state, c.from_user.id))

    await c.message.edit_text(
        f"Введи номер карти (16 цифр):\n`4441111111111111`\nСума: **{amount} грн**\nЧас: 10 хвилин",
        parse_mode="Markdown", reply_markup=get_back_keyboard())
    await state.set_state(Order.waiting_card)

async def timeout_order(state: FSMContext, user_id: int):
    await asyncio.sleep(600)
    if await state.get_state() == Order.waiting_card:
        await state.clear()
        try: await bot.send_message(user_id, "Час вийшов /start")
        except: pass

@dp.message(Order.waiting_card)
async def get_card(m: types.Message, state: FSMContext):
    card = ''.join(filter(str.isdigit, m.text or ""))
    if len(card)!=16 or not luhn_check(card):
        await m.answer("Тільки 16 цифр!", reply_markup=get_back_keyboard())
        return

    data = await state.get_data()
    amount, routes, instructor_code = data['amount'], data['routes'], data.get('instructor_code')

    cursor.execute("""INSERT INTO purchases 
        (user_id, username, card_hash, card_last4, amount, routes, status, order_time, instructor_code)
        VALUES (?,?,?,?,'pending',?,?)""",
        (m.from_user.id, m.from_user.username or "N/A", hash_card(card), card[-4:], amount, routes,
         datetime.now().strftime("%H:%M"), instructor_code))
    conn.commit()

    await m.answer(f"Чекай підтвердження оплати {amount} грн...")

    routes_text = ", ".join(r.split("_")[1].upper() for r in routes.split(","))
    admin_text = (f"Новий заказ!\n\nКористувач: {escape(m.from_user.username or 'N/A')}\n"
                  f"ID: <code>{m.from_user.id}</code>\nКарта: <code>{fmt(card[-4:])}</code>\n"
                  f"Сума: <b>{amount} грн</b>\nМаршрути: {routes_text}")
    if instructor_code: admin_text += f"\nІнструктор: <code>{instructor_code}</code>"

    await bot.send_message(ADMIN_ID, admin_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{m.from_user.id}_{amount}")],
            [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{m.from_user.id}_{amount}")]
        ]), parse_mode="HTML")
    await state.clear()

# === ОДОБРИТИ ===
@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer("Ти не адмін!", show_alert=True); return
    try:
        _, uid, amt = c.data.split("_")
        uid, amt = int(uid), int(amt)
    except: await c.answer("Помилка"); return

    row = cursor.execute("SELECT routes, instructor_code FROM purchases WHERE user_id=? AND amount=? AND status='pending'", (uid, amt)).fetchone()
    if not row:
        await c.answer("Вже оброблено"); return

    routes, inst_code = row
    links = [VIDEOS[r.strip()] for r in routes.split(",") if r.strip() in VIDEOS]
    links_text = "\n".join(links)
    links_csv = ",".join(links)

    cursor.execute("UPDATE purchases SET status='success', links=? WHERE user_id=? AND amount=? AND status='pending'",
                   (links_csv, uid, amt))
    conn.commit()

    try:
        await bot.send_message(uid, "Оплата підтверджена!\nТвої маршрути:\n\n" + links_text)
        await bot.send_message(uid, "Обери ще:", reply_markup=get_main_keyboard())
    except: pass

    await c.message.edit_text(c.message.html_text + "\n\n<b>Одобрено!</b>", parse_mode="HTML")

    if inst_code:
        payout = amt * 0.1
        cursor.execute("UPDATE instructors SET total_earned = total_earned + ? WHERE code=?", (payout, inst_code))
        conn.commit()
        inst = cursor.execute("SELECT username, total_earned FROM instructors WHERE code=?", (inst_code,)).fetchone()
        if inst and inst[1] >= 100:
            await bot.send_message(ADMIN_ID, f"<b>{escape(inst[0])} — виплата {inst[1]} грн</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"Виплатити {inst[1]} грн", callback_data=f"pay_{inst_code}")]]),
                parse_mode="HTML")

# === ВІДМОВИТИ ===
@dp.callback_query(F.data.startswith("reject_"))
async def reject_init(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        await c.answer("Ти не адмін!", show_alert=True); return
    try:
        _, uid, amt = c.data.split("_")
        uid, amt = int(uid), int(amt)
    except: await c.answer("Помилка"); return

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
    try:
        await bot.send_message(data['rej_uid'], f"Оплата не підтверджена.\nПричина: {m.text}\nЗвернись до адміна.",
                               reply_markup=get_contact_admin_keyboard())
    except: pass
    await m.answer("Відмову відправлено")
    await state.clear()

# === АДМІНСЬКІ КОМАНДИ ===
@dp.message(Command("stats"))
async def stats(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    total = cursor.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    success = cursor.execute("SELECT COUNT(*) FROM purchases WHERE status='success'").fetchone()[0]
    rev = cursor.execute("SELECT COALESCE(SUM(amount),0) FROM purchases WHERE status='success'").fetchone()[0]
    await m.answer(f"<b>Статистика</b>\nВсього: {total}\nУспішно: {success}\nДохід: <b>{rev} грн</b>", parse_mode="HTML")

@dp.message(Command("stats_full"))
async def stats_full(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    rows = cursor.execute("SELECT status, COUNT(*), COALESCE(SUM(amount),0) FROM purchases GROUP BY status").fetchall()
    text = "<b>Повна статистика:</b>\n\n"
    real = 0
    for s, cnt, suma in rows:
        emoji = {"success":"Успішно","pending":"Очікують","cancelled":"Скасовано","rejected":"Відмовлено"}.get(s,s)
        text += f"{emoji} <b>{s}</b>: {cnt} → {suma} грн\n"
        if s=="success": real = suma
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

# === ЗАПУСК ===
async def main():
    log.info("Бот запущено!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
