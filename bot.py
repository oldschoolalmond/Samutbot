import os
import asyncio
import logging
import aiosqlite
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Update

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# 1. Загрузка переменных из Railway
TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Например: https://your-app.up.railway.app
WEBHOOK_PATH = f"/bot/{TOKEN}"

# 2. Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояния для диалога
class Form(StatesGroup):
    password = State()
    select_group = State()
    get_text = State()

# 3. База данных (SQLite)
async def init_db():
    async with aiosqlite.connect("bot_data.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS groups (title TEXT, chat_id INTEGER PRIMARY KEY)")
        await db.commit()

# 4. Жизненный цикл FastAPI (установка вебхука)
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(url=url, drop_pending_updates=True)
    logging.info(f"Webhook set to: {url}")
    yield
    await bot.delete_webhook()

# ЭТО ТА САМАЯ ПЕРЕМЕННАЯ, КОТОРУЮ ИЩЕТ UVICORN
app = FastAPI(lifespan=lifespan)

# 5. Обработка запросов от Telegram
@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def index():
    return {"status": "bot is running"}

# --- ЛОГИКА БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Введите пароль для управления ботом:")
    await state.set_state(Form.password)

@dp.message(Form.password)
async def check_pass(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        builder = InlineKeyboardBuilder()
        async with aiosqlite.connect("bot_data.db") as db:
            async with db.execute("SELECT title, chat_id FROM groups") as cursor:
                async for row in cursor:
                    builder.row(types.InlineKeyboardButton(text=row[0], callback_data=f"grp_{row[1]}"))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("📍 Список групп пуст.\nДобавьте бота в группу и напишите там /reg")
            await state.clear()
            return

        await message.answer("✅ Доступ разрешен. Выберите группу:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Неверный пароль. Попробуйте еще раз /start")
        await state.clear()

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?)", (message.chat.title, message.chat.id))
            await db.commit()
        await message.answer(f"✅ Группа '{message.chat.title}' успешно добавлена в базу!")
    else:
        await message.answer("⚠️ Эту команду нужно писать внутри группы.")

@dp.callback_query(Form.select_group, F.data.startswith("grp_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data.split("_")[1]
    await state.update_data(target_id=chat_id)
    await callback.message.edit_text("📝 Введите текст сообщения для публикации:")
    await state.set_state(Form.get_text)

@dp.message(Form.get_text)
async def post_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(chat_id=data['target_id'], text=message.text)
        await message.answer("🚀 Сообщение успешно опубликовано!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
