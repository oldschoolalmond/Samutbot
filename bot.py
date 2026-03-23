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

# 1. НАСТРОЙКА ЛОГИРОВАНИЯ
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. ПЕРЕМЕННЫЕ
TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
BASE_URL = os.getenv("WEBHOOK_URL") 
WEBHOOK_PATH = "/tg/webhook"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class Form(StatesGroup):
    password = State()
    select_group = State()
    get_content = State() # Изменили название: теперь ждем контент, а не только текст

# 3. БАЗА ДАННЫХ
async def init_db():
    async with aiosqlite.connect("bot_data.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                display_name TEXT, 
                chat_id INTEGER, 
                thread_id INTEGER,
                PRIMARY KEY (chat_id, thread_id)
            )
        """)
        await db.commit()

# 4. ЖИЗНЕННЫЙ ЦИКЛ (Webhook)
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
        logger.info(f"✅ Вебхук активен: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Ошибка вебхука: {e}")
    yield
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@app.post("/tg/webhook")
async def bot_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки: {e}")
    return {"ok": True}

@app.get("/")
async def index():
    return {"status": "Бот готов к пересылке файлов"}

# --- ЛОГИКА БОТА ---

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Введите пароль:")
    await state.set_state(Form.password)

@dp.message(Form.password)
async def check_pass(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        builder = InlineKeyboardBuilder()
        async with aiosqlite.connect("bot_data.db") as db:
            async with db.execute("SELECT display_name, chat_id, thread_id FROM groups") as cursor:
                async for row in cursor:
                    t_id = row[2] if row[2] else 0
                    builder.row(types.InlineKeyboardButton(
                        text=row[0], 
                        callback_data=f"send_{row[1]}_{t_id}"
                    ))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("📍 База пуста. Напишите /reg в группе.")
            await state.clear()
            return

        await message.answer("✅ Пароль верный. Куда отправить сообщение?", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Ошибка в пароле.")
        await state.clear()

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        chat_id = message.chat.id
        thread_id = message.message_thread_id
        display_name = f"{message.chat.title}" + (f" | Тема: {thread_id}" if thread_id else "")

        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute(
                "INSERT OR REPLACE INTO groups (display_name, chat_id, thread_id) VALUES (?, ?, ?)",
                (display_name, chat_id, thread_id)
            )
            await db.commit()
        await message.answer(f"✅ Группа сохранена: {display_name}")

@dp.callback_query(Form.select_group, F.data.startswith("send_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(target_chat_id=int(parts[1]), target_thread_id=int(parts[2]) if int(parts[2]) != 0 else None)
    await callback.message.edit_text("📤 Теперь пришлите всё что угодно: текст, фото, стикер или файл.")
    await state.set_state(Form.get_content)

# ФИНАЛЬНЫЙ ЭТАП: Копируем любое сообщение
@dp.message(Form.get_content)
async def post_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        # Используем copy_message — это перешлет сообщение "как свое", 
        # сохранив картинку, подпись или стикер.
        await bot.copy_message(
            chat_id=data['target_chat_id'],
            message_thread_id=data['target_thread_id'],
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        await message.answer("🚀 Опубликовано!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()
