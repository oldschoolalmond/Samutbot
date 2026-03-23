import os
import aiosqlite
import uvicorn
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv

load_dotenv()

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
BOT_PASSWORD = os.getenv("BOT_PASSWORD") # Добавь эту переменную в Railway!
DB_PATH = "/app/data/bot_data.db" if os.path.exists("/app/data") else "bot_data.db"

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

async def init_db():
    if "/app/data" in DB_PATH and not os.path.exists("/app/data"):
        os.makedirs("/app/data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица топиков
        await db.execute("CREATE TABLE IF NOT EXISTS topics (chat_id INTEGER, thread_id INTEGER, name TEXT, PRIMARY KEY(chat_id, name))")
        # Состояние пользователя + флаг авторизации (is_auth)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY, 
                chat_id INTEGER, 
                thread_id INTEGER, 
                is_auth INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# --- Логика в группах ---
@dp.message(Command("save_topic"), F.chat.type.in_({"group", "supergroup"}))
async def save_topic(message: types.Message):
    name = message.text.replace("/save_topic", "").strip()
    if name:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO topics VALUES (?, ?, ?)", 
                             (message.chat.id, message.message_thread_id, name))
            await db.commit()
        await message.answer(f"✅ Раздел '{name}' сохранен.")

# --- Логика в личке ---
@dp.message(CommandStart(), F.chat.type == "private")
async def start_private(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_auth FROM user_states WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            is_auth = row[0] if row else 0

    if is_auth:
        # Если уже авторизован, показываем группы
        await show_groups(message)
    else:
        # Если нет — просим пароль
        await message.answer("🔒 Доступ ограничен. Пожалуйста, введите пароль для использования бота:", reply_markup=ReplyKeyboardRemove())

async def show_groups(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT chat_id FROM topics") as cursor:
            groups = await cursor.fetchall()
    
    if not groups:
        return await message.answer("Разделы еще не настроены админом в группах.")

    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=f"Группа: {g[0]}")] for g in groups], resize_keyboard=True)
    await message.answer("Выберите группу:", reply_markup=kb)

@dp.message(F.chat.type == "private")
async def handle_private(message: types.Message):
    user_id = message.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_auth, chat_id, thread_id FROM user_states WHERE user_id = ?", (user_id,)) as cursor:
            user_data = await cursor.fetchone()
        
        is_auth = user_data[0] if user_data else 0
        current_chat_id = user_data[1] if user_data else None

        # 1. ПРОВЕРКА ПАРОЛЯ (если еще не авторизован)
        if not is_auth:
            if message.text == BOT_PASSWORD:
                await db.execute("INSERT OR REPLACE INTO user_states (user_id, is_auth) VALUES (?, 1)", (user_id,))
                await db.commit()
                await message.answer("✅ Пароль верный! Доступ открыт.")
                return await show_groups(message)
            else:
                return await message.answer("❌ Неверный пароль. Попробуйте еще раз:")

        # 2. ВЫБОР ГРУППЫ
        if message.text and message.text.startswith("Группа: "):
            try:
                target_chat_id = int(message.text.replace("Группа: ", ""))
                async with db.execute("SELECT name FROM topics WHERE chat_id = ?", (target_chat_id,)) as cursor:
                    topics = await cursor.fetchall()
                
                kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t[0])] for t in topics], resize_keyboard=True)
                await db.execute("UPDATE user_states SET chat_id = ?, thread_id = NULL WHERE user_id = ?", (target_chat_id, user_id))
                await db.commit()
                return await message.answer(f"Выберите раздел:", reply_markup=kb)
            except: pass

        # 3. ВЫБОР РАЗДЕЛА
        if current_chat_id:
            async with db.execute("SELECT thread_id FROM topics WHERE chat_id = ? AND name = ?", (current_chat_id, message.text)) as cursor:
                topic = await cursor.fetchone()
            if topic:
                await db.execute("UPDATE user_states SET thread_id = ? WHERE user_id = ?", (topic[0], user_id))
                await db.commit()
                return await message.answer(f"✅ Готово! Отправьте ваш пост.")

        # 4. ОТПРАВКА
        async with db.execute("SELECT chat_id, thread_id FROM user_states WHERE user_id = ?", (user_id,)) as cursor:
            state = await cursor.fetchone()
            
    if state and state[0] and state[1]:
        try:
            await bot.copy_message(chat_id=state[0], from_chat_id=message.chat.id, message_id=message.message_id, message_thread_id=state[1])
            await message.answer("🚀 Опубликовано!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
    else:
        await message.answer("⚠️ Выберите группу и раздел.")

# --- Запуск ---
@app.on_event("startup")
async def on_startup():
    await init_db()
    clean_url = WEBHOOK_URL.strip("/")
    await bot.set_webhook(url=f"{clean_url}/webhook", drop_pending_updates=True)

@app.post("/webhook")
async def webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
