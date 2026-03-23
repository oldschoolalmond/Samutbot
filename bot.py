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

# Настройка логирования (чтобы видеть ошибки в логах Railway)
logging.basicConfig(level=logging.INFO)

# 1. ЗАГРУЗКА НАСТРОЕК (из переменных Railway)
TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
BASE_URL = os.getenv("WEBHOOK_URL") 
WEBHOOK_PATH = "/tg/webhook"

# 2. ИНИЦИАЛИЗАЦИЯ БОТА
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояния для диалога (этапы общения с пользователем)
class Form(StatesGroup):
    password = State()      # Ожидание пароля
    select_group = State()  # Ожидание выбора группы
    get_text = State()      # Ожидание текста для поста

# 3. БАЗА ДАННЫХ (SQLite)
# Создаем таблицу, которая хранит: название, ID чата и ID темы (топика)
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

# 4. УСТАНОВКА СВЯЗИ С ТЕЛЕГРАМ (Webhook)
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Собираем полный адрес: https://ваш-проект.railway.app/tg/webhook
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    logging.info(f"Установка вебхука на: {webhook_url}")
    
    # Говорим Телеграму отправлять сообщения по этому адресу
    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )
    yield
    await bot.delete_webhook()

# Создаем веб-сервер, который Railway сможет запустить
app = FastAPI(lifespan=lifespan)

# Принимаем данные от Телеграма
@app.post("/tg/webhook")
async def bot_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.error(f"Ошибка обработки обновления: {e}")
    return {"ok": True}

# Проверка, что сервер жив (если зайти на сайт бота через браузер)
@app.get("/")
async def index():
    return {"status": "Бот запущен и работает!"}

# --- ЛОГИКА РАБОТЫ БОТА ---

# Команда /reg — Запоминает группу или конкретную тему (топик)
@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        chat_id = message.chat.id
        thread_id = message.message_thread_id # ID топика
        
        # Формируем имя для кнопки (Название группы + название темы, если есть)
        group_title = message.chat.title
        topic_name = ""
        if message.is_topic_message:
            topic_name = f" | Тема ID: {thread_id}"
        
        display_name = f"{group_title}{topic_name}"

        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute(
                "INSERT OR REPLACE INTO groups (display_name, chat_id, thread_id) VALUES (?, ?, ?)",
                (display_name, chat_id, thread_id)
            )
            await db.commit()
        
        await message.answer(f"✅ Успешно! Теперь я могу писать в: **{display_name}**", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Эту команду нужно писать в группе или в конкретной теме форума.")

# Команда /start — Начало работы в личке у бота
@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Введите пароль для доступа к рассылке:")
    await state.set_state(Form.password)

# Проверка пароля
@dp.message(Form.password)
async def check_pass(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        builder = InlineKeyboardBuilder()
        async with aiosqlite.connect("bot_data.db") as db:
            async with db.execute("SELECT display_name, chat_id, thread_id FROM groups") as cursor:
                async for row in cursor:
                    # Упаковываем данные в кнопку. 0 если темы нет.
                    t_id = row[2] if row[2] else 0
                    builder.row(types.InlineKeyboardButton(
                        text=row[0], 
                        callback_data=f"send_{row[1]}_{t_id}"
                    ))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("📍 База групп пуста. Сначала добавьте меня в группу и напишите там /reg")
            await state.clear()
            return

        await message.answer("🔓 Доступ разрешен! Выберите чат для отправки:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Неверный пароль. Попробуйте еще раз /start")
        await state.clear()

# Когда нажали на кнопку с выбором группы
@dp.callback_query(Form.select_group, F.data.startswith("send_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    chat_id = int(parts[1])
    thread_id = int(parts[2]) if int(parts[2]) != 0 else None
    
    await state.update_data(target_chat_id=chat_id, target_thread_id=thread_id)
    await callback.message.edit_text("📝 Напишите сообщение, которое нужно опубликовать:")
    await state.set_state(Form.get_text)

# Отправка итогового сообщения
@dp.message(Form.get_text)
async def post_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(
            chat_id=data['target_chat_id'],
            message_thread_id=data['target_thread_id'], # Магия отправки в нужную тему
            text=message.text
        )
        await message.answer("🚀 Готово! Сообщение опубликовано.")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")
    await state.clear()
