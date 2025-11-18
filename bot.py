# ХУСТ ПДР БОТ — ФІНАЛЬНА РОБОЧА ВЕРСІЯ (останні 4 цифри + підтримка + закриття спору)
import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv
from html import escape

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PRICE_SINGLE = int(os.getenv('PRICE_SINGLE', '100'))
PRICE_ALL = int(os.getenv('PRICE_ALL', '350'))
ADMIN_CARD = os.getenv('ADMIN_CARD')

if not all([BOT_TOKEN, ADMIN_ID, ADMIN_CARD]):
    raise ValueError("Заповни .env: BOT_TOKEN, ADMIN_ID, ADMIN_CARD")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# База
conn = sqlite3.connect('/data/purchases.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, username TEXT, card_last4 TEXT,
    amount INTEGER, routes TEXT, status TEXT,
    order_time TEXT, links TEXT, instructor_code TEXT)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS instructors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE, username TEXT, card TEXT, total_earned REAL DEFAULT 0)''')

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

# Стани
class Order(StatesGroup):
    waiting_last4 = State()
    waiting_reject_reason = State()

class Support(StatesGroup):
    waiting_message = State()

# Клавіатури
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Маршрут №1 — {PRICE_SINGLE} грн", callback_data="buy_route1")],
        [InlineKeyboardButton(text=f"Маршрут №8 — {PRICE_SINGLE} грн", callback_data="buy_route8")],
        [InlineKeyboardButton(text=f"Маршрут №6 — {PRICE_SINGLE} грн", callback_data="buy_route6")],
        [InlineKeyboardButton(text=f"Маршрут №2 — {PRICE_SINGLE} грн", callback_data="buy_route2")],
        [InlineKeyboardButton(text=f"Всі 4 маршрути — {PRICE_ALL} грн", callback_data="buy_all")],
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]])

def get_contact_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написати адміну", callback_data="contact_admin")]])

# START
@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("inst_"):
        await state.update_data(instructor_code=args[1].split("_",1)[1])

    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (m.from_user.id,)).fetchone()
    if row and row[0]:
        await m.answer("Твої маршрути:\n\n" + row[0].replace(',', '\n'), reply_markup=get_main_keyboard())
    else:
        await m.answer(
            f"Вітаю в Хуст ПДР Бот!\n\n"
            f"Обери маршрут → оплати → введи останні 4 цифри карти → отримай відео!\n\n"
            f"Ціна: {PRICE_SINGLE} грн │ Всі 4 — {PRICE_ALL} грн\n\n"
            f"Карта:\n`{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`",
            reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back")
async def back(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Обери маршрут:", reply_markup=get_main_keyboard())

# ПОКУПКА
@dp.callback_query(F.data.startswith("buy_"))
async def buy(c: types.CallbackQuery, state: FSMContext):
    if cursor.execute("SELECT 1 FROM purchases WHERE user_id=? AND status='pending'", (c.from_user.id,)).fetchone():
        return await c.answer("У тебе вже є активне замовлення!", show_alert=True)

    map = {
        "buy_route1": ("khust_route1", PRICE_SINGLE),
        "buy_route8": ("khust_route8", PRICE_SINGLE),
        "buy_route6": ("khust_route6", PRICE_SINGLE),
        "buy_route2": ("khust_route2", PRICE_SINGLE),
        "buy_all": (",".join(VIDEOS.keys()), PRICE_ALL),
    }
    routes, amount = map[c.data]
    await state.update_data(amount=amount, routes=routes)

    await c.message.edit_text(
        f"Введи **останні 4 цифри** карти, з якої оплатив:\n\nПриклад: `4321`\nСума: **{amount} грн**",
        parse_mode="Markdown", reply_markup=get_back_keyboard())
    await state.set_state(Order.waiting_last4)

@dp.message(Order.waiting_last4)
async def get_last4(m: types.Message, state: FSMContext):
    last4 = ''.join(filter(str.isdigit, m.text or ""))
    if len(last4) != 4:
        return await m.answer("Помилка! Введи рівно 4 цифри.", reply_markup=get_back_keyboard())

    data = await state.get_data()
    amount, routes = data['amount'], data['routes']
    inst_code = data.get('instructor_code')

    cursor.execute("""INSERT INTO purchases
        (user_id, username, card_last4, amount, routes, status, order_time, instructor_code)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (m.from_user.id, m.from_user.username or "N/A", last4, amount, routes,
         datetime.now().strftime("%H:%M"), inst_code))
    conn.commit()

    await m.answer("Дякую! Чекайде перевірка оплати...")

    routes_txt = ", ".join(r.split("_")[1].upper() for r in routes.split(","))
    admin_msg = (f"Новий заказ!\n\n"
                 f"Користувач: @{escape(m.from_user.username or 'N/A')}\n"
                 f"ID: <code>{m.from_user.id}</code>\n"
                 f"Останні цифри: <b>{last4}</b>\n"
                 f"Сума: <b>{amount} грн</b>\n"
                 f"Маршрути: {routes_txt}")
    if inst_code: admin_msg += f"\nРеферал: <code>{inst_code}</code>"

    await bot.send_message(ADMIN_ID, admin_msg,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{m.from_user.id}_{amount}")],
            [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{m.from_user.id}_{amount}"]]
        ]), parse_mode="HTML")
    await state.clear()

# ОДОБРИТИ
@dp.callback_query(F.data.startswith("approve_"))
async def approve(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    try:
        _, uid, amt = c.data.split("_")
        uid, amt = int(uid), int(amt)
    except: return

    row = cursor.execute("SELECT routes, instructor_code FROM purchases WHERE user_id=? AND amount=? AND status='pending'", (uid, amt)).fetchone()
    if not row: return await c.answer("Вже оброблено")

    routes, inst = row
    links = [VIDEOS[r.strip()] for r in routes.split(",") if r.strip() in VIDEOS]
    links_text = "\n".join(links)
    links_csv = ",".join(links)

    cursor.execute("UPDATE purchases SET status='success', links=? WHERE user_id=? AND amount=?", (links_csv, uid, amt))
    conn.commit()

    await bot.send_message(uid, "Оплата підтверджена!\nОсь твої відео:\n\n" + links_text)
    await bot.send_message(uid, "Можеш купити ще:", reply_markup=get_main_keyboard())
    await c.message.edit_text(c.message.html_text + "\n\nОдобрено та видано!", parse_mode="HTML")

    if inst:
        cursor.execute("UPDATE instructors SET total_earned = total_earned + ? WHERE code=?", (amt*0.1, inst))
        conn.commit()

# ВІДМОВИТИ
@dp.callback_query(F.data.startswith("reject_"))
async def reject(c: types.CallbackQuery, state: FSMContext):
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

    await bot.send_message(data['rej_uid'],
        f"Оплата не підтверджена.\nПричина: {m.text}\nЗвернись до адміна.",
        reply_markup=get_contact_admin_keyboard())
    await m.answer("Відмову відправлено")
    await state.clear()

# ПІДТРИМКА
@dp.callback_query(F.data == "contact_admin")
async def contact_admin(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.answer("Напиши своє питання — адмін отримає його миттєво:",
                            reply_markup=ReplyKeyboardRemove())
    await state.set_state(Support.waiting_message)
    await state.update_data(user_id=c.from_user.id, username=c.from_user.username or "Без імені")

@dp.message(Support.waiting_message)
async def forward_support(m: types.Message, state: FSMContext):
    data = await state.get_data()
    text = (f"Повідомлення в підтримку:\n"
            f"Від: @{escape(data['username'])} (ID: {data['user_id']})\n\n"
            f"{escape(m.text)}")
    await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    await m.answer("Повідомлення відправлено! Чекай відповідь.")
    await state.clear()

# ЗАКРИТТЯ СПОРУ (відповідь адміна)
@dp.message(F.reply_to_message & F.from_user.id == ADMIN_ID)
async def close_dispute(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    orig = m.reply_to_message
    if not orig or "Повідомлення в підтримку" not in orig.text: return

    try:
        user_id = int(orig.text.split("ID: ")[1].split(")")[0])
    except: return

    await bot.send_message(user_id, f"Відповідь адміна:\n\n{m.text}")
    await m.edit_text(m.text + "\n\nСпір закрито", reply_markup=None)

# АДМІН КОМАНДИ
@dp.message(Command("stats_full"))
async def stats_full(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    rows = cursor.execute("SELECT status, COUNT(*), COALESCE(SUM(amount),0) FROM purchases GROUP BY status").fetchall()
    text = "<b>Повна статистика:</b>\n\n"; real = 0
    for s,cnt,suma in rows:
        e = {"success":"Успішно","pending":"Очікують","cancelled":"Скасовано","rejected":"Відмовлено"}.get(s,s)
        text += f"{e} <b>{s}</b>: {cnt} → {suma} грн\n"
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
    await m.answer(f"Видалено {d} записів!")

@dp.message(Command("my"))
async def my(m: types.Message):
    row = cursor.execute("SELECT links FROM purchases WHERE user_id=? AND status='success' ORDER BY id DESC LIMIT 1", (m.from_user.id,)).fetchone()
    if row and row[0]:
        await m.answer("Твої маршрути:\n\n" + row[0].replace(',','\n'))
    else:
        await m.answer("Нічого не куплено")

# ЗАПУСК
async def main():
    log.info("Бот запущено!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
