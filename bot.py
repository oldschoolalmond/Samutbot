import os
import aiosqlite
import uvicorn
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DB_PATH = "/app/data/bot_data.db" if os.path.exists("/app/data") else "bot_data.db"

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

async def init_db():
    if "/app/data" in DB_PATH and not os.path.exists("/app/data"):
        os.makedirs("/app/data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Теперь храним chat_id группы для каждого раздела
        await db.execute("CREATE TABLE IF NOT EXISTS topics (chat_id INTEGER, thread_id INTEGER, name TEXT, PRIMARY KEY(chat_id, name))")
        # Состояние пользователя: в какой группе и в каком разделе он сейчас
        await db.execute("CREATE TABLE IF NOT EXISTS user_states (user_id INTEGER PRIMARY KEY, chat_id INTEGER, thread_id INTEGER)")
        await db.commit()

# --- Логика в ГРУППАХ ---
@dp.message(Command("save_topic"), F.chat.type.in_({"group", "supergroup"}))
async def save_topic(message: types.Message):
    name = message.text.replace("/save_topic", "").strip()
    if name:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO topics VALUES (?, ?, ?)", 
                             (message.chat.id, message.message_thread_id, name))
            await db.commit()
        await message.answer(f"✅ Section '{name}' saved for THIS group.")

# --- Логика в ЛИЧКЕ ---
@dp.message(CommandStart(), F.chat.type == "private")
async def start_private(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        # Находим все уникальные группы, где есть разделы
        async with db.execute("SELECT DISTINCT chat_id FROM topics") as cursor:
            groups = await cursor.fetchall()
    
    if not groups:
        return await message.answer("No active groups found. Setup a group first!")

    # Для простоты выведем список ID групп или их названий (если бот их знает)
    # Но лучше сразу предлагать выбор разделов, если группа одна, или выбор групп
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=f"Group: {g[0]}")] for g in groups], resize_keyboard=True)
    await message.answer("Select a group or send a message if you already selected one:", reply_markup=kb)

@dp.message(F.chat.type == "private")
async def handle_private(message: types.Message):
    user_id = message.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Если пользователь выбирает ГРУППУ (по ID для примера)
        if message.text and message.text.startswith("Group: "):
            try:
                target_chat_id = int(message.text.replace("Group: ", ""))
                async with db.execute("SELECT name FROM topics WHERE chat_id = ?", (target_chat_id,)) as cursor:
                    topics = await cursor.fetchall()
                
                kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t[0])] for t in topics], resize_keyboard=True)
                # Временно запоминаем chat_id, но без thread_id
                await db.execute("INSERT OR REPLACE INTO user_states (user_id, chat_id) VALUES (?, ?)", (user_id, target_chat_id))
                await db.commit()
                return await message.answer(f"Now select a section in this group:", reply_markup=kb)
            except: pass

        # 2. Если пользователь выбирает РАЗДЕЛ (по названию)
        async with db.execute("SELECT chat_id FROM user_states WHERE user_id = ?", (user_id,)) as cursor:
            state = await cursor.fetchone()
        
        if state and state[0]:
            chat_id = state[0]
            async with db.execute("SELECT thread_id FROM topics WHERE chat_id = ? AND name = ?", (chat_id, message.text)) as cursor:
                topic = await cursor.fetchone()
            
            if topic:
                await db.execute("UPDATE user_states SET thread_id = ? WHERE user_id = ?", (topic[0], user_id))
                await db.commit()
                return await message.answer(f"✅ Ready! Sending to '{message.text}'. Send your post now.")

        # 3. ОТПРАВКА СООБЩЕНИЯ
        async with db.execute("SELECT chat_id, thread_id FROM user_states WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
    if row and row[0] and row[1]:
        try:
            await bot.copy_message(chat_id=row[0], from_chat_id=message.chat.id, message_id=message.message_id, message_thread_id=row[1])
            await message.answer("🚀 Published anonymous message!")
        except Exception as e:
            await message.answer(f"❌ Error: {e}")
    else:
        await message.answer("⚠️ Please select a group and a section first.")

# --- Стандартный запуск вебхука ---
@app.on_event("startup")
async def on_startup():
    await init_db()
    await bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)

@app.post("/webhook")
async def webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
