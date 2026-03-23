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

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = f"/bot/{TOKEN}"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class Form(StatesGroup):
    password = State()
    select_group = State()
    get_text = State()

async def init_db():
    async with aiosqlite.connect("bot_data.db") as db:
        # Добавляем колонку thread_id (может быть NULL, если это обычная группа)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                display_name TEXT, 
                chat_id INTEGER, 
                thread_id INTEGER,
                PRIMARY KEY (chat_id, thread_id)
            )
        """)
        await db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}", drop_pending_updates=True)
    yield
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

# --- ЛОГИКА С ТОПИКАМИ ---

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        chat_id = message.chat.id
        thread_id = message.message_thread_id # ID топика (None если нет тем)
        
        # Определяем название для меню
        group_title = message.chat.title
        topic_name = ""
        
        # Если это форум, пытаемся понять название темы
        if message.is_topic_message and message.reply_to_message:
            # В aiogram 3.x информация о топике часто сидит в forum_topic_created или в кастомных полях
            topic_name = f" | Тема ID: {thread_id}"
        
        display_name = f"{group_title}{topic_name}"

        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute(
                "INSERT OR REPLACE INTO groups (display_name, chat_id, thread_id) VALUES (?, ?, ?)",
                (display_name, chat_id, thread_id)
            )
            await db.commit()
        
        await message.answer(f"✅ Зарегистрировано как: **{display_name}**", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Пишите это в группе или топике.")

@dp.message(Command("start"))
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
                    # Кодируем chat_id и thread_id в callback_data
                    t_id = row[2] if row[2] else 0
                    builder.row(types.InlineKeyboardButton(
                        text=row[0], 
                        callback_data=f"send_{row[1]}_{t_id}"
                    ))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("База пуста. Напишите /reg в нужных топиках.")
            await state.clear()
            return

        await message.answer("Куда отправляем?", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Нет.")

@dp.callback_query(Form.select_group, F.data.startswith("send_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    chat_id = int(parts[1])
    thread_id = int(parts[2]) if int(parts[2]) != 0 else None
    
    await state.update_data(target_chat_id=chat_id, target_thread_id=thread_id)
    await callback.message.edit_text("📝 Пишите текст сообщения:")
    await state.set_state(Form.get_text)

@dp.message(Form.get_text)
async def post_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(
            chat_id=data['target_chat_id'],
            message_thread_id=data['target_thread_id'], # ПУБЛИКАЦИЯ В ТОПИК
            text=message.text
        )
        await message.answer("🚀 Улетело в чат!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
