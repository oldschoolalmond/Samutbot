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

# 2. ПЕРЕМЕННЫЕ (из настроек Railway)
TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
BASE_URL = os.getenv("WEBHOOK_URL") 
WEBHOOK_PATH = "/tg/webhook"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояния диалога
class Form(StatesGroup):
    password = State()      # Ожидание пароля
    select_group = State()  # Выбор чата
    get_content = State()   # Ожидание того, что переслать
    confirm = State()       # Подтверждение отправки

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
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"✅ Вебхук установлен: {webhook_url}")
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
    return {"status": "Бот с предпросмотром запущен"}

# --- ЛОГИКА БОТА ---

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Введите пароль для управления:")
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
                        callback_data=f"target_{row[1]}_{t_id}"
                    ))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("📍 База пуста. Напишите /reg в нужной группе.")
            await state.clear()
            return

        await message.answer("🔓 Доступ разрешен. Выберите чат:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        chat_id = message.chat.id
        thread_id = message.message_thread_id
        display_name = f"{message.chat.title}" + (f" | Тема: {thread_id}" if thread_id else "")

        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?, ?)", (display_name, chat_id, thread_id))
            await db.commit()
        await message.answer(f"✅ Запомнил: {display_name}")

@dp.callback_query(Form.select_group, F.data.startswith("target_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(target_chat_id=int(parts[1]), target_thread_id=int(parts[2]) if int(parts[2]) != 0 else None)
    await callback.message.edit_text("📥 Пришлите то, что хотите опубликовать (текст, фото, файл или стикер):")
    await state.set_state(Form.get_content)

# ПРЕДПРОСМОТР
@dp.message(Form.get_content)
async def preview_content(message: types.Message, state: FSMContext):
    # Сохраняем ID сообщения, которое нужно будет скопировать
    await state.update_data(msg_to_copy=message.message_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Подтвердить и отправить", callback_data="confirm_send"))
    builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_send"))
    
    await message.answer("👀 Вот так это будет выглядеть. Отправляем?", reply_markup=builder.as_markup())
    await state.set_state(Form.confirm)

# ФИНАЛЬНАЯ ОТПРАВКА
@dp.callback_query(Form.confirm, F.data == "confirm_send")
async def final_send(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.copy_message(
            chat_id=data['target_chat_id'],
            message_thread_id=data['target_thread_id'],
            from_chat_id=callback.message.chat.id,
            message_id=data['msg_to_copy']
        )
        await callback.message.edit_text("🚀 Опубликовано!")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
    await state.clear()

@dp.callback_query(Form.confirm, F.data == "cancel_send")
async def cancel_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📂 Отменено. Чтобы начать заново, напишите /start")
    await state.clear()
