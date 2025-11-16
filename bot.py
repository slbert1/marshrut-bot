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

class Order(StatesGroup):
    waiting_card = State()
    waiting_reject_reason = State()

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
        (message.from_user.id, message.from_user.username or "N/A", card, amount, routes, order_time)
    )
    conn.commit()

    await message.answer(
        f"Оплата: **{amount} грн**\n"
        f"Карта: `{formatted_card}`\n"
        f"Переведи на:\n"
        f"`{ADMIN_CARD[:4]} {ADMIN_CARD[4:8]} {ADMIN_CARD[8:12]} {ADMIN_CARD[12:]}`\n"
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
        [InlineKeyboardButton(text="Одобрити", callback_data=f"approve_{message.from_user.id}_{amount}")],
        [InlineKeyboardButton(text="Відмовити", callback_data=f"reject_{message.from_user.id}_{amount}")],  # ← УНИФИЦИРОВАНО
    ])
    try:
        await bot.send_message(ADMIN_ID, admin_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Не вдалося надіслати адміну: {e}")
    await state.clear()

# === ОТКАЗ: Сразу запрашиваем причину ===
@dp.callback_query(F.data.startswith("reject_"))
async def reject_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return

    try:
        _, user_id, amount = callback.data.split("_", 2)  # ← 2 части
        user_id, amount = int(user_id), int(amount)
    except Exception as e:
        await callback.answer("Помилка даних.")
        log.error(f"Reject parse error: {e}, data: {callback.data}")
        return

    await state.update_data(reject_user_id=user_id, reject_amount=amount)
    await state.set_state(Order.waiting_reject_reason)
    await callback.message.edit_text(
        f"{callback.message.text}\n\n"
        f"Введіть причину відмови:",
        parse_mode="Markdown"
    )

# === ОТКАЗ: Причина введена ===
@dp.message(Order.waiting_reject_reason)
async def reject_with_reason(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Причина занадто коротка. Введіть ще раз:")
        return

    data = await state.get_data()
    user_id = data['reject_user_id']
    amount = data['reject_amount']

    cursor.execute("UPDATE purchases SET status='rejected' WHERE user_id=? AND amount=?", (user_id, amount))
    conn.commit()

    client_text = (
        f"Вибачте, {reason}.\n"
        f"Зв'яжіться з адміністратором бота для уточнень."
    )

    try:
        await bot.send_message(
            user_id,
            client_text,
            reply_markup=get_contact_admin_keyboard()
        )
        await asyncio.sleep(1)
        # Возврат в меню
        dummy_msg = types.Message(
            chat=types.Chat(id=user_id, type='private'),
            from_user=types.User(id=user_id, is_bot=False, first_name=''),
            text='/start',
            message_id=0
        )
        await start(dummy_msg, state)
    except Exception as e:
        log.warning(f"Не вдалося надіслати клієнту {user_id}: {e}")

    await message.answer(f"Відмову відправлено: {reason}")
    await state.clear()

# === КНОПКА "Написати адміну" ===
@dp.callback_query(F.data == "contact_admin")
async def contact_admin(callback: types.CallbackQuery):
    await callback.message.answer("Напишіть ваше повідомлення, і воно буде надіслано адміністратору.")

@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ти не адмін!", show_alert=True)
        return

    try:
        _, user_id, amount = callback.data.split("_", 2)
        user_id, amount = int(user_id), int(amount)
    except Exception as e:
        await callback.answer("Помилка даних.")
        log.error(f"Approve parse error: {e}")
        return

    row = cursor.execute(
        "SELECT routes FROM purchases WHERE user_id=? AND amount=? AND status='pending'",
        (user_id, amount)
    ).fetchone()

    if not row:
        await callback.answer("Замовлення вже оброблено.")
        return

    routes = row[0]
    links = [VIDEOS[r] for r in routes.split(',')]
    links_text = "\n".join(links)

    cursor.execute(
        "UPDATE purchases SET status='success', links=? WHERE user_id=? AND amount=?",
        (",".join(links), user_id, amount)
    )
    conn.commit()

    await send_videos(user_id, links_text)
    await callback.message.edit_text(f"{callback.message.text}\n\nОдобрено!", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, "Оплата підтверджена! Відео надіслано.")
    except Exception as e:
        log.warning(f"Юзер {user_id} заблокував бота: {e}")

async def send_videos(user_id: int, links_text: str):
    text = "Оплата підтверджена!\nТвої маршрути:\n\n" + links_text
    try:
        await bot.send_message(user_id, text)
        await bot.send_message(user_id, "Обери ще маршрут:", reply_markup=get_main_keyboard())
    except Exception as e:
        log.warning(f"Не вдалося надіслати відео {user_id}: {e}")

# === ЗАПУСК ===
async def main():
    log.info("Бот запускається на Render Background Worker...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
