import os
import asyncio
import aiohttp
import sqlite3
import time
from datetime import date
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
ADMIN_ID = 5143085326  # ТВОЙ TELEGRAM ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === БАЗА ДАННЫХ ===
conn = sqlite3.connect('purchases.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        order_id TEXT,
        invoice_id TEXT UNIQUE,
        routes TEXT,
        amount INTEGER,
        payment_date TIMESTAMP,
        status TEXT
    )
''')
conn.commit()

# === ВИДЕО — ТОЛЬКО ХУСТ ===
VIDEOS = {
    'khust': {
        'route1': {'name': '№1', 'url': 'https://youtu.be/mxtsqKmXWSI'},
        'route8': {'name': '№8', 'url': 'https://youtu.be/7VwtAAaQWE8'},
        'route6': {'name': '№6', 'url': 'https://youtu.be/RnpOEKIddZw'},
        'route2': {'name': '№2', 'url': 'https://youtu.be/RllCGT6dOPc'},
    }
}

class Cart(StatesGroup):
    viewing_routes = State()

# === КОМАНДА /MYID (для админа) ===
@dp.message(Command('myid'))
async def myid_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Команда тільки для адміна.")
        return
    await message.answer(f"Твій ID: {message.from_user.id}")

# === СТАРТ ===
@dp.message(Command('start'))
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) > 1 and args[1].startswith('paid_'):
        await manual_paid(message)
        return

    was_here = cursor.execute(
        "SELECT 1 FROM purchases WHERE user_id=?", (user_id,)
    ).fetchone()

    if was_here:
        await message.answer(
            "З поверненням!\n\nОбери маршрут:",
            reply_markup=get_routes_keyboard()
        )
        await state.set_state(Cart.viewing_routes)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="СТАРТ", callback_data="begin_bot")]
    ])
    await message.answer(
        "Вітаю! Це бот з екзаменаційними маршрутами\n\n"
        "Натисни кнопку нижче — і почнемо",
        reply_markup=kb
    )

# === КНОПКА "СТАРТ" ===
@dp.callback_query(F.data == "begin_bot")
async def begin_bot(callback: types.CallbackQuery, state: FSMContext):
    welcome_text = (
        "Вітаю! Це бот з екзаменаційними маршрутами для водіїв — Хуст\n\n"
        "Як користуватися:\n"
        "1. Обери маршрут або «Всі маршрути»\n"
        "2. Оплати будь-якою картою (250 грн за один, 1000 грн за всі)\n"
        "3. Відео прийде автоматично в цю ж переписку\n\n"
        "Після оплати — відео доступне лише тобі\n"
        "Ніхто не бачить твої дані\n\n"
        "Готовий? Обери нижче"
    )
    await callback.message.edit_text(welcome_text)
    await show_khust_routes(callback.message, state)

# === КНОПКИ МАРШРУТОВ ===
def get_routes_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Маршрут №1 — 250 грн", callback_data="buy_khust_route1")],
        [InlineKeyboardButton(text="Маршрут №8 — 250 грн", callback_data="buy_khust_route8")],
        [InlineKeyboardButton(text="Маршрут №6 — 250 грн", callback_data="buy_khust_route6")],
        [InlineKeyboardButton(text="Маршрут №2 — 250 грн", callback_data="buy_khust_route2")],
        [InlineKeyboardButton(text="Всі 4 маршрути — 1000 грн", callback_data="buy_khust_all")],
    ])

async def show_khust_routes(message: types.Message | types.CallbackQuery, state: FSMContext):
    kb = get_routes_keyboard()
    text = "Маршрути — Хуст\n\nОбери:"
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)
    await state.set_state(Cart.viewing_routes)

# === ПОКУПКА ===
@dp.callback_query(F.data.startswith("buy_khust_"))
async def buy(callback: types.CallbackQuery, state: FSMContext):
    route_key = callback.data.split("_")[-1]
    amount = 1000 if route_key == "all" else 250
    desc = "Всі 4 маршрути — Хуст" if route_key == "all" else f"Маршрут {VIDEOS['khust'][route_key]['name']} — Хуст"
    routes = ",".join(VIDEOS['khust'].keys()) if route_key == "all" else route_key

    user_id = callback.from_user.id
    order_id = f"{user_id}_{int(time.time())}"

    try:
        invoice_id = await create_mono_invoice(amount, order_id, desc)
        await callback.message.edit_text(
            f"Оплати {desc} ({amount} грн):\nhttps://pay.monobank.ua/{invoice_id}\n\n(Будь-яка карта)",
            disable_web_page_preview=True
        )
        cursor.execute(
            "INSERT OR REPLACE INTO purchases (user_id, order_id, invoice_id, routes, amount, payment_date, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (user_id, order_id, invoice_id, routes, amount, time.strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        print(f"[DB] Saved: {invoice_id}")
    except Exception as e:
        await callback.message.edit_text(f"Помилка: {e}")
        print(f"[ERROR] {e}")

# === MONO INVOICE (БОЕВОЙ) ===
async def create_mono_invoice(amount: int, order_id: str, desc: str):
    data = {
        'amount': amount * 100,
        'ccy': 980,
        'merchantPaymInfo': {'reference': order_id, 'description': desc},
        'webHookUrl': 'https://marshrut-bot.onrender.com/webhook',  # БОЕВОЙ URL
        'redirectUrl': f"https://t.me/ExamenPdr_bot?start=paid_{order_id}"
    }
    headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
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
        for r in row[0].split(','):
            name = VIDEOS['khust'][r]['name']
            url = VIDEOS['khust'][r]['url']
            text += f"Маршрут {name}: {url}\n"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Продовжити роботу", callback_data="continue_shopping")]
        ])
        await message.answer(text + "\n\n", reply_markup=kb)
    else:
        await message.answer("Оплата ще обробляється. Зачекай 1-2 хв.")

# === КНОПКА "ПРОДОВЖИТИ РОБОТУ" ===
@dp.callback_query(F.data == "continue_shopping")
async def continue_shopping(callback: types.CallbackQuery, state: FSMContext):
    await show_khust_routes(callback.message, state)

# === АВТО-ПРОВЕРКА + СТАТИСТИКА ===
async def check_pending_payments():
    while True:
        await asyncio.sleep(10)
        rows = cursor.execute("SELECT invoice_id, user_id, routes, amount FROM purchases WHERE status='pending'").fetchall()
        for invoice_id, user_id, routes, amount in rows:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                    headers = {'X-Token': MONO_TOKEN}
                    async with session.get(f'https://api.monobank.ua/api/merchant/invoice/status?invoiceId={invoice_id}', headers=headers) as resp:
                        data = await resp.json()
                        if data.get('status') == 'success':
                            cursor.execute("UPDATE purchases SET status='paid' WHERE invoice_id=?", (invoice_id,))
                            conn.commit()

                            text = "Оплата пройшла! Твої маршрути:\n\n"
                            for r in routes.split(','):
                                name = VIDEOS['khust'][r]['name']
                                url = VIDEOS['khust'][r]['url']
                                text += f"Маршрут {name}: {url}\n"
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Продовжити роботу", callback_data="continue_shopping")]
                            ])
                            await bot.send_message(user_id, text + "\n\n", reply_markup=kb)

                            await send_admin_stats(invoice_id, user_id, routes, amount)
                            print(f"[AUTO] Sent to {user_id}")
            except Exception as e:
                if "APPLICATION_DATA_AFTER_CLOSE_NOTIFY" in str(e):
                    continue
                print(f"[CHECK] Error: {e}")

# === СТАТИСТИКА ДЛЯ АДМИНА (с именем и @username) ===
async def send_admin_stats(invoice_id, user_id, routes, amount):
    try:
        user = await bot.get_chat(user_id)
        name = user.first_name or "Без імені"
        username = f"@{user.username}" if user.username else ""
        user_str = f"{name} {username}".strip()
    except:
        user_str = f"ID: {user_id} (немає даних)"

    purchase_detail = f"ID покупки: {invoice_id}\n"
    purchase_detail += f"Користувач: {user_str}\n"
    purchase_detail += f"Маршрути: {routes}\n"
    purchase_detail += f"Сума: {amount} грн\n"
    purchase_detail += f"Дата: {time.strftime('%d.%m.%Y %H:%M')}"

    today = date.today()
    first_day_month = today.replace(day=1)
    first_day_year = today.replace(month=1, day=1)

    day_amount = cursor.execute(
        "SELECT SUM(amount) FROM purchases WHERE status='paid' AND DATE(payment_date) = ?", (today,)
    ).fetchone()[0] or 0
    month_amount = cursor.execute(
        "SELECT SUM(amount) FROM purchases WHERE status='paid' AND DATE(payment_date) >= ?", (first_day_month,)
    ).fetchone()[0] or 0
    year_amount = cursor.execute(
        "SELECT SUM(amount) FROM purchases WHERE status='paid' AND DATE(payment_date) >= ?", (first_day_year,)
    ).fetchone()[0] or 0

    stats_text = f"СТАТИСТИКА:\n"
    stats_text += f"День: {day_amount} грн\n"
    stats_text += f"Місяць: {month_amount} грн\n"
    stats_text += f"Рік: {year_amount} грн"

    if year_amount > 900000:
        stats_text += f"\nБлизько до ліміту НБУ (1 млн грн/рік)! Плануй ФОП."
    elif year_amount > 500000:
        stats_text += f"\nЛіміт НБУ (1 млн грн/рік) — стеж за доходами."

    full_text = purchase_detail + "\n\n" + stats_text
    await bot.send_message(ADMIN_ID, full_text)

# === РЕГИСТРАЦИЯ WEBHOOK В MONO (ПРАВИЛЬНОЕ МЕСТО!) ===
async def register_webhook():
    try:
        async with aiohttp.ClientSession() as session:
            headers = {'X-Token': MONO_TOKEN, 'Content-Type': 'application/json'}
            data = {
                'webHookUrl': 'https://marshrut-bot.onrender.com/webhook'
            }
            async with session.post('https://api.monobank.ua/api/merchant/webhook/set', json=data, headers=headers) as resp:
                result = await resp.json()
                print(f"[WEBHOOK] Registration: {result}")
    except Exception as e:
        print(f"[WEBHOOK] Error: {e}")

# === ЗАПУСК ===
async def main():
    print("Бот запущен (БОЕВОЙ РЕЖИМ, webhook, статистика)...")
    await register_webhook()  # Теперь видит функцию!
    await asyncio.gather(
        dp.start_polling(bot),
        check_pending_payments()
    )

if __name__ == '__main__':
    asyncio.run(main())
