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

# 1. LOGGING SETTINGS
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. VARIABLES (from Railway settings)
TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
BASE_URL = os.getenv("WEBHOOK_URL") 
WEBHOOK_PATH = "/tg/webhook"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Conversation States
class Form(StatesGroup):
    password = State()      
    select_group = State()  
    get_content = State()   
    confirm = State()       

# 3. DATABASE
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

# 4. LIFECYCLE (Webhook)
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
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
        logger.error(f"❌ Processing error: {e}")
    return {"ok": True}

@app.get("/")
async def index():
    return {"status": "Bot is running"}

# --- BOT LOGIC ---

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Please enter the password:")
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
            await message.answer("📍 Database is empty. Use /reg in a group first.")
            await state.clear()
            return

        await message.answer("🔓 Access granted. Select a chat:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Invalid password!")
        await state.clear()

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        chat_id = message.chat.id
        thread_id = message.message_thread_id
        
        group_title = message.chat.title
        topic_name = ""
        
        # Logic to find Topic Name instead of ID
        if message.is_topic_message:
            # Try to find the topic name from the forum_topic_created object or current context
            # If it's a regular message in a topic, we use a placeholder or ID if name is unknown
            topic_name = f" | Topic: {thread_id}" 
            # Note: Telegram API doesn't always send the Topic Name with every message.
            # To get the real name, you'd need extra permissions or a specific service message.
        
        display_name = f"{group_title}{topic_name}"

        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?, ?)", (display_name, chat_id, thread_id))
            await db.commit()
        await message.answer(f"✅ Registered: **{display_name}**", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Use this command inside a group or forum topic.")

@dp.callback_query(Form.select_group, F.data.startswith("target_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(target_chat_id=int(parts[1]), target_thread_id=int(parts[2]) if int(parts[2]) != 0 else None)
    await callback.message.edit_text("📥 Send the content you want to publish (text, photo, file, or sticker):")
    await state.set_state(Form.get_content)

# PREVIEW
@dp.message(Form.get_content)
async def preview_content(message: types.Message, state: FSMContext):
    await state.update_data(msg_to_copy=message.message_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Confirm & Send", callback_data="confirm_send"))
    builder.row(types.InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_send"))
    
    await message.answer("👀 This is a preview. Shall I send it?", reply_markup=builder.as_markup())
    await state.set_state(Form.confirm)

# FINAL SEND
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
        await callback.message.edit_text("🚀 Published successfully!")
    except Exception as e:
        await callback.message.edit_text(f"❌ Error: {e}")
    await state.clear()

@dp.callback_query(Form.confirm, F.data == "cancel_send")
async def cancel_send(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📂 Cancelled. Type /start to begin again.")
    await state.clear()
