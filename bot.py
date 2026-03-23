import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Update

# 1. LOGGING & SETTINGS
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_PASSWORD = os.getenv("BOT_PASSWORD")
BASE_URL = os.getenv("WEBHOOK_URL") 
WEBHOOK_PATH = "/tg/webhook"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# 2. HARDCODED DATA (Your Links)
# Format: "Group Name": {"Topic Name": (Chat_ID, Thread_ID)}
GROUPS_DATA = {
    "VIP 1": {
        "Sinyal": (-1003637027634, 7)
    },
    "VIP 2": {
        "Gmeet": (-1003798167669, 2),
        "Chat": (-1003798167669, 5),
        "Q&A": (-1003798167669, 9)
    },
    "VIP 3": {
        "Gmeet": (-1003778789398, 5),
        "Q&A": (-1003778789398, 7),
        "Chat": (-1003778789398, 3)
    }
}

class Form(StatesGroup):
    password = State()      
    select_group = State()  
    select_topic = State()
    get_content = State()   
    confirm = State()       

# 3. FASTAPI & WEBHOOK
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logger.info(f"✅ Webhook set to: {webhook_url}")
    yield
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@app.post("/tg/webhook")
async def bot_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def index():
    return {"status": "Bot is ready. Built-in topics loaded."}

# --- BOT LOGIC (ENGLISH) ---

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Please enter the admin password:")
    await state.set_state(Form.password)

@dp.message(Form.password)
async def check_pass(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        builder = InlineKeyboardBuilder()
        for group_name in GROUPS_DATA.keys():
            builder.row(types.InlineKeyboardButton(text=group_name, callback_data=f"grp_{group_name}"))
        
        await message.answer("🔓 Access granted. Select VIP Group:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("❌ Wrong password. Try again /start")
        await state.clear()

@dp.callback_query(Form.select_group, F.data.startswith("grp_"))
async def group_selected(callback: types.CallbackQuery, state: FSMContext):
    group_name = callback.data.split("_")[1]
    builder = InlineKeyboardBuilder()
    
    topics = GROUPS_DATA.get(group_name, {})
    for topic_name in topics.keys():
        builder.row(types.InlineKeyboardButton(text=topic_name, callback_data=f"top_{group_name}_{topic_name}"))
    
    await callback.message.edit_text(f"Selected: {group_name}. Now select a Topic:", reply_markup=builder.as_markup())
    await state.set_state(Form.select_topic)

@dp.callback_query(Form.select_topic, F.data.startswith("top_"))
async def topic_selected(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    group_name, topic_name = parts[1], parts[2]
    
    chat_id, thread_id = GROUPS_DATA[group_name][topic_name]
    await state.update_data(target_chat_id=chat_id, target_thread_id=thread_id)
    
    await callback.message.edit_text(f"Target: {group_name} > {topic_name}\n📤 Send your content (text, photo, or file):")
    await state.set_state(Form.get_content)

@dp.message(Form.get_content)
async def preview_msg(message: types.Message, state: FSMContext):
    await state.update_data(mid=message.message_id)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Confirm & Send", callback_data="yes"),
                types.InlineKeyboardButton(text="❌ Cancel", callback_data="no"))
    
    await message.answer("👀 This is a preview. Shall I publish it?", reply_markup=builder.as_markup())
    await state.set_state(Form.confirm)

@dp.callback_query(Form.confirm, F.data == "yes")
async def send_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.copy_message(
            chat_id=data['target_chat_id'],
            message_thread_id=data['target_thread_id'],
            from_chat_id=callback.message.chat.id,
            message_id=data['mid']
        )
        await callback.message.edit_text("🚀 Published successfully!")
    except Exception as e:
        await callback.message.edit_text(f"❌ Error: {e}\nMake sure the bot is an Admin in the group.")
    await state.clear()

@dp.callback_query(F.data == "no")
async def cancel_action(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Cancelled. Use /start to begin again.")
    await state.clear()
