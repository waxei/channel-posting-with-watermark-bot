import os
import asyncio
from typing import Any, Dict, Union
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InputMediaPhoto, Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.methods.get_file import GetFile
from PIL import Image
from dotenv import load_dotenv


# Конфигурация
dotent_path = os.path.join(os.path.dirname(__file__), '.env')
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
WATERMARK_PATH = "watermark.png"
AUTH_KEY = os.getenv("SECRET_KEY")  # Статичный кюч для авторизации

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Middleware для обработки альбомов
class AlbumMiddleware(BaseMiddleware):
    def __init__(self, latency: Union[int, float] = 0.1):
        self.latency = latency
        self.album_data = {}

    def collect_album_messages(self, event: Message):
        if event.media_group_id not in self.album_data:
            self.album_data[event.media_group_id] = {"messages": []}
        self.album_data[event.media_group_id]["messages"].append(event)
        return len(self.album_data[event.media_group_id]["messages"])

    async def __call__(self, handler, event: Message, data: Dict[str, Any]) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        total_before = self.collect_album_messages(event)
        await asyncio.sleep(self.latency)

        # Проверяем, существует ли еще media_group_id в album_data
        if event.media_group_id not in self.album_data:
            return

        total_after = len(self.album_data[event.media_group_id]["messages"])

        if total_before != total_after:
            return

        album_messages = self.album_data[event.media_group_id]["messages"]
        album_messages.sort(key=lambda x: x.message_id)
        data["album"] = album_messages
        del self.album_data[event.media_group_id]
        return await handler(event, data)

dp.message.middleware(AlbumMiddleware())

# Состояния
class PostStates(StatesGroup):
    waiting_for_auth = State()  # Ожидание ключа авторизации
    waiting_for_content = State()  # Ожидание контента для поста

# Создаем папку для временных файлов
os.makedirs("temp", exist_ok=True)

def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Создать пост"))
    return builder.as_markup(resize_keyboard=True)

def cancel_menu():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Отмена"))
    return builder.as_markup(resize_keyboard=True)

def add_watermark(image_path, output_path):
    try:
        base_image = Image.open(image_path).convert("RGBA")
        watermark = Image.open(WATERMARK_PATH).convert("RGBA")

        wm_width = int(base_image.width * 0.20)
        aspect_ratio = watermark.height / watermark.width
        wm_height = int(wm_width * aspect_ratio)
        watermark = watermark.resize((wm_width, wm_height))

        position = (base_image.width - watermark.width - 30, 30)
        transparent = Image.new('RGBA', base_image.size, (0, 0, 0, 0))
        transparent.paste(watermark, position, mask=watermark)

        watermarked = Image.alpha_composite(base_image, transparent)
        watermarked.convert("RGB").save(output_path)
        return True
    except Exception as e:
        print(f"Watermark error: {e}")
        return False

# Команда /start
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.set_state(PostStates.waiting_for_auth)
    await message.answer(
        "Введите ключ авторизации:",
        reply_markup=cancel_menu()
    )

# Обработка ключа авторизации
@dp.message(PostStates.waiting_for_auth)
async def process_auth(message: types.Message, state: FSMContext):
    if message.text == AUTH_KEY:
        await state.set_state(PostStates.waiting_for_content)
        await message.answer(
            "Авторизация успешна! Теперь вы можете создавать посты.",
            reply_markup=main_menu()
        )
    else:
        await message.answer("Неверный ключ авторизации. Попробуйте снова.")

# Создание поста
@dp.message(F.text == "Создать пост", PostStates.waiting_for_content)
async def create_post(message: types.Message, state: FSMContext):
    await state.set_state(PostStates.waiting_for_content)
    await message.answer(
        "Отправьте пост (текст, фото или альбом):",
        reply_markup=cancel_menu()
    )

# Отмена создания поста
@dp.message(F.text == "Отмена", PostStates.waiting_for_content)
async def cancel_post(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено", reply_markup=main_menu())

# Обработка контента
@dp.message(PostStates.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext, album: list[Message] = None):
    processed_files = []
    media_group = []
    caption = None

    try:
        # Обработка альбома
        if album:
            caption = album[0].caption or None
            for msg in album:
                if msg.photo:
                    file_id = msg.photo[-1].file_id
                    file = await bot(GetFile(file_id=file_id))
                    original_path = f"temp/orig_{file_id}.jpg"
                    await bot.download_file(file.file_path, original_path)

                    output_path = f"temp/wm_{file_id}.jpg"
                    if add_watermark(original_path, output_path):
                        processed_files.append((original_path, output_path))
                        media_group.append(InputMediaPhoto(
                            media=FSInputFile(output_path),
                            caption=caption if len(media_group) == 0 else None
                        ))

        # Обработка одиночного сообщения
        else:
            if message.photo:
                file_id = message.photo[-1].file_id
                file = await bot(GetFile(file_id=file_id))
                original_path = f"temp/orig_{file_id}.jpg"
                await bot.download_file(file.file_path, original_path)

                output_path = f"temp/wm_{file_id}.jpg"
                if add_watermark(original_path, output_path):
                    processed_files.append((original_path, output_path))
                    media_group.append(InputMediaPhoto(
                        media=FSInputFile(output_path),
                        caption=message.caption or message.text
                    ))
            elif message.text:
                caption = message.text

        # Отправка контента
        if media_group:
            await bot.send_media_group(CHANNEL_ID, media=media_group)
        elif caption:
            await bot.send_message(CHANNEL_ID, caption)

        await message.answer("✅ Пост опубликован!", reply_markup=main_menu())

    except Exception as e:
        await message.answer(f"🚨 Ошибка: {str(e)}")

    finally:
        # Очистка временных файлов
        for original, output in processed_files:
            try:
                os.remove(original)
                os.remove(output)
            except:
                pass
        await state.set_state(PostStates.waiting_for_content)

if __name__ == "__main__":
    print("Бот запущен!")
    dp.run_polling(bot)