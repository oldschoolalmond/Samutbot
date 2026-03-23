import os
import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

# Состояния
class Form(StatesGroup):
    password = State()
    select_group = State()
    get_text = State()

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Инициализация БД
async def init_db():
    async with aiosqlite.connect("bot_data.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS groups (title TEXT, chat_id INTEGER PRIMARY KEY)")
        await db.commit()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🔐 Введите пароль:")
    await state.set_state(Form.password)

@dp.message(Form.password)
async def check_pass(message: types.Message, state: FSMContext):
    if message.text == os.getenv("BOT_PASSWORD"):
        builder = InlineKeyboardBuilder()
        async with aiosqlite.connect("bot_data.db") as db:
            async with db.execute("SELECT title, chat_id FROM groups") as cursor:
                async for row in cursor:
                    builder.row(types.InlineKeyboardButton(text=row[0], callback_data=f"grp_{row[1]}"))
        
        if not builder.as_markup().inline_keyboard:
            await message.answer("Список групп пуст. Напишите /reg в группе.")
            await state.clear()
            return

        await message.answer("Выберите группу:", reply_markup=builder.as_markup())
        await state.set_state(Form.select_group)
    else:
        await message.answer("Неверно!")

@dp.message(Command("reg"))
async def reg_group(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?)", (message.chat.title, message.chat.id))
            await db.commit()
        await message.answer(f"✅ Группа '{message.chat.title}' сохранена в БД!")

@dp.callback_query(Form.select_group, F.data.startswith("grp_"))
async def group_chosen(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data.split("_")[1]
    await state.update_data(target_id=chat_id)
    await callback.message.edit_text("📝 Введите текст для публикации:")
    await state.set_state(Form.get_text)

@dp.message(Form.get_text)
async def post_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(chat_id=data['target_id'], text=message.text)
        await message.answer("🚀 Опубликовано!")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    await state.clear()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
