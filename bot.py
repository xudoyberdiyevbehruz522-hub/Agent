import asyncio
import logging
import os
from collections import defaultdict, deque
from pathlib import Path

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv
from google import genai
from google.genai import errors
from google.genai import types as genai_types

# .env faylini yuklash
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("gemini-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN topilmadi! .env faylini tekshiring.")
if not GEMINI_KEY:
    raise ValueError("GEMINI_API_KEY topilmadi! .env faylini tekshiring.")

def read_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default

def read_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

# Asosiy sozlamalar
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
MAX_TURNS = read_positive_int("MAX_HISTORY_TURNS", 10)
TEMPERATURE = read_float("TEMPERATURE", 0.7)
REQUIRE_ADMIN_APPROVAL = os.getenv("REQUIRE_ADMIN_APPROVAL", "True").lower() in ("true", "1", "yes")

ADMIN_USER_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_USER_IDS", "").split(",")
    if item.strip().isdigit()
}

# Tugmalar matni
BTN_CHAT = "🤖 AI bilan suhbat"
BTN_RESET = "🧹 Suhbatni tozalash"
BTN_HELP = "ℹ️ Yordam"
BTN_MYID = "🆔 Mening ID raqamim"
BTN_LANGUAGE = "🌐 Tilni tanlash"
BTN_FEEDBACK = "💬 Fikr-mulohaza"
BTN_CANCEL = "❌ Bekor qilish"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_CHAT)],
        [KeyboardButton(text=BTN_RESET), KeyboardButton(text=BTN_HELP)],
        [KeyboardButton(text=BTN_MYID), KeyboardButton(text=BTN_LANGUAGE)],
        [KeyboardButton(text=BTN_FEEDBACK)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Savolingizni yozing yoki fayl yuboring...",
)

LANGUAGE_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇺🇿 O‘zbekcha", callback_data="lang:uz")],
    [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru")],
    [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")],
])
LANGUAGES = {"uz": "o‘zbek tilida", "ru": "rus tilida", "en": "ingliz tilida"}

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = genai.Client(
    api_key=GEMINI_KEY,
    
)

histories = defaultdict(lambda: deque(maxlen=MAX_TURNS * 2))
locks = defaultdict(asyncio.Lock)
user_languages = defaultdict(lambda: "uz")

def is_allowed(user_id: int) -> bool:
    if not REQUIRE_ADMIN_APPROVAL:
        return True
    return user_id in ADMIN_USER_IDS

class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data: dict):
        if not isinstance(event, types.Message):
            return await handler(event, data)
        
        if not REQUIRE_ADMIN_APPROVAL:
            return await handler(event, data)

        user = event.from_user
        if user and is_allowed(user.id):
            return await handler(event, data)

        text = event.text or ""
        first_word = text.split(maxsplit=1)[0] if text.strip() else ""
        command_name = first_word.split("@", maxsplit=1)[0].lower()

        if command_name in {"/start", "/help", "/myid", "/cancel"} or text in {
            BTN_MYID, BTN_HELP, BTN_FEEDBACK, BTN_CANCEL
        }:
            return await handler(event, data)

        state = data.get("state")
        if state:
            current_state = await state.get_state()
            if current_state == FeedbackState.waiting_for_feedback.state:
                return await handler(event, data)

        await event.answer(
            "⛔ AI va shaxsiy sozlamalardan foydalanish uchun administrator ruxsati kerak.\n\n"
            "🆔 ID raqamingizni bilish uchun «Mening ID raqamim»ni bosing.\n"
            "💬 Fikr yoki taklif yuborish uchun «Fikr-mulohaza»ni bosing.",
            reply_markup=MAIN_KEYBOARD,
        )
        return None

dp.message.outer_middleware(AccessMiddleware())

def split_text(text: str, limit: int = 4000) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks or ["AI bo‘sh javob qaytardi."]

def error_message(exc: Exception) -> str:
    if isinstance(exc, errors.APIError):
        code = exc.code
        if code == 429:
            return "⚠️ Gemini API limiti yoki kvotasi tugadi. Google AI Studio sozlamalarini tekshiring."
        if code == 404:
            return "⚠️ Gemini modeli topilmadi. .env faylidagi GEMINI_MODEL qiymatini tekshiring."
        if code in (400, 401, 403):
            return "⚠️ API so‘rovi rad etildi. API kaliti, model va ruxsatlarni tekshiring."
        if code is not None and code >= 500:
            return "⚠️ Gemini serverida muammo. Keyinroq urinib ko‘ring."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "⏱ Javob kutish vaqti tugadi. Qayta urinib ko‘ring."
    if isinstance(exc, ValueError):
        return str(exc)
    return "⚠️ Xatolik yuz berdi. Keyinroq qayta urinib ko‘ring."

async def generate(user_id: int, parts: list[genai_types.Part], store_parts: list[genai_types.Part]) -> str:
    history = histories[user_id]
    language = user_languages[user_id]
    system_prompt = (
        "Sen foydali Telegram AI yordamchisisan. "
        f"Javoblarni {LANGUAGES.get(language, LANGUAGES['uz'])} ber. "
        "Aniq, tushunarli matnlardan foydalan, kerak bo'lsa Markdown bilan chiroyli qilib formatla."
    )
    config = genai_types.GenerateContentConfig(system_instruction=system_prompt, temperature=TEMPERATURE)
    contents = list(history) + [genai_types.Content(role="user", parts=parts)]
    response = await client.aio.models.generate_content(model=MODEL, contents=contents, config=config)
    answer = (response.text or "").strip()
    
    if answer:
        history.append(genai_types.Content(role="user", parts=store_parts))
        history.append(genai_types.Content(role="model", parts=[genai_types.Part(text=answer)]))
    return answer

async def process(message: types.Message, parts: list[genai_types.Part], store_parts: list[genai_types.Part]) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    async with locks[user_id]:
        status = None
        try:
            status = await message.answer("⏳ Javob tayyorlanmoqda...")
            async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
                answer = await generate(user_id, parts, store_parts)
        except Exception as exc:
            log.exception("Gemini xatosi. user_id=%s", user_id)
            error = error_message(exc)
            if status:
                try:
                    await status.edit_text(error)
                    return
                except Exception:
                    pass
            await message.answer(error)
            return
        
        if not answer:
            answer = "AI javob qaytarmadi. Savolni boshqacha yozib ko‘ring."
            
        if status:
            try:
                await status.delete()
            except Exception:
                pass
                
    for chunk in split_text(answer):
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
        except TelegramBadRequest:
            try:
                await message.answer(chunk)
            except Exception:
                log.exception("Javobni Telegramga yuborib bo‘lmadi.")
                break
        except Exception:
            log.exception("Kutilmagan xatolik xabar yuborishda.")
            break

# Yordamchi yuklab olish funksiyasi (20 MB gacha cheklov bilan)
async def download_media(file_id: str, bot_instance: Bot) -> bytes:
    file_info = await bot_instance.get_file(file_id)
    if file_info.file_size and file_info.file_size > 20 * 1024 * 1024:
        raise ValueError("⚠️ Fayl hajmi 20 MB dan katta. Telegram bot faqat 20 MB gacha bo'lgan fayllarni yuklay oladi.")
    buffer = await bot_instance.download(file_info)
    if buffer is None:
        raise ValueError("⚠️ Faylni yuklab bo‘lmadi.")
    return buffer.getvalue()

@dp.message(Command("myid"))
@dp.message(F.text == BTN_MYID)
async def myid_handler(message: types.Message):
    if not message.from_user:
        return
    await message.answer(
        f"🆔 Sizning Telegram ID'ingiz: `{message.from_user.id}`\n"
        "Ruxsat olish uchun ushbu raqamni administratorga yuboring.",
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    welcome_text = (
        "👋 *Assalomu alaykum!*\n\n"
        "🤖 Agent yordamchi botiga xush kelibsiz!\n\n"
        "✨ *BOT IMKONIYATLARI*\n"
        "• 💬 Savollarga javob olish\n"
        "• 🖼️ Rasmlarni tahlil qilish\n"
        "• 📄 PDF va boshqa hujjatlarni o'qish\n"
        "• 🎬 Videolar va GIF'larni tushunish\n"
        "• 🎤 Ovozli va Audio xabarlarni tahlil qilish\n"
        "• 🌐 Ko'p tilli muloqot (O'zbek, Rus, Ingliz)\n\n"
        "👇 Boshlash uchun menyudan foydalaning yoki xabar yuboring:"
    )
    await message.answer(welcome_text, reply_markup=MAIN_KEYBOARD, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def help_handler(message: types.Message):
    await message.answer(
        "ℹ️ *Botdan foydalanish:*\n\n"
        "Istalgan *matn, rasm, video, audio yoki hujjat* yuboring — bot ularni tahlil qilib beradi.\n\n"
        "🧹 /reset — Suhbat tarixini tozalash\n"
        "🌐 *Tilni tanlash* — javob tilini o‘zgartirish\n"
        "💬 *Fikr-mulohaza* — adminlarga taklif yuborish\n",
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("reset"))
@dp.message(F.text == BTN_RESET)
async def reset_handler(message: types.Message):
    if message.from_user:
        histories.pop(message.from_user.id, None)
    await message.answer("🧹 Suhbat tarixi tozalandi. AI oldingi gaplarni unutdi.", reply_markup=MAIN_KEYBOARD)

@dp.message(F.text == BTN_CHAT)
async def chat_button_handler(message: types.Message):
    await message.answer(
        "🤖 AI bilan suhbat boshlandi.\nEndi istalgan savolni yozing yoki fayl yuboring (Rasm, Video, Audio, Hujjat).",
        reply_markup=MAIN_KEYBOARD,
    )

@dp.message(F.text == BTN_LANGUAGE)
async def language_handler(message: types.Message):
    await message.answer("🌐 Javoblar tilini tanlang:", reply_markup=LANGUAGE_KEYBOARD)

@dp.callback_query(F.data.startswith("lang:"))
async def language_callback(callback: types.CallbackQuery):
    if not callback.from_user:
        await callback.answer()
        return
    if REQUIRE_ADMIN_APPROVAL and not is_allowed(callback.from_user.id):
        await callback.answer("Tilni o‘zgartirish uchun administrator ruxsati kerak.", show_alert=True)
        return
    language = (callback.data or "").split(":", 1)[1]
    if language not in LANGUAGES:
        await callback.answer("Noto‘g‘ri til tanlandi.")
        return
    user_languages[callback.from_user.id] = language
    names = {"uz": "O‘zbekcha", "ru": "Русский", "en": "English"}
    await callback.answer("Til o‘zgartirildi!")
    if callback.message and isinstance(callback.message, types.Message):
        await callback.message.edit_text(f"✅ Javob tili: *{names[language]}*", parse_mode=ParseMode.MARKDOWN)
        await callback.message.answer("Endi suhbatni davom ettirishingiz mumkin.", reply_markup=MAIN_KEYBOARD)

@dp.message(F.text == BTN_FEEDBACK)
async def feedback_start(message: types.Message, state: FSMContext):
    await state.set_state(FeedbackState.waiting_for_feedback)
    await message.answer(
        "💬 Fikringizni yoki taklifingizni bitta xabar qilib yuboring.\nBekor qilish uchun /cancel ni bosing.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True),
    )

@dp.message(Command("cancel"), StateFilter(FeedbackState.waiting_for_feedback))
@dp.message(F.text == BTN_CANCEL, StateFilter(FeedbackState.waiting_for_feedback))
async def feedback_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Fikr-mulohaza yuborish bekor qilindi.", reply_markup=MAIN_KEYBOARD)

@dp.message(StateFilter(FeedbackState.waiting_for_feedback), F.text)
async def feedback_receive(message: types.Message, state: FSMContext):
    if not message.from_user:
        return
    feedback = (message.text or "").strip()
    if not feedback:
        await message.answer("Iltimos, bo‘sh bo‘lmagan matn yuboring.")
        return
    if len(feedback) > 3000:
        await message.answer("Fikr-mulohaza juda uzun. 3000 belgidan kamroq yuboring.")
        return
    if not ADMIN_USER_IDS:
        await state.clear()
        await message.answer("⚠️ Administrator sozlanmagan, fikr-mulohaza yuborilmadi.", reply_markup=MAIN_KEYBOARD)
        return
    delivered = False
    for admin_id in ADMIN_USER_IDS:
        try:
            await bot.send_message(
                admin_id,
                "💬 *Yangi fikr-mulohaza*\n\n"
                f"👤 *Foydalanuvchi:* {message.from_user.full_name}\n"
                f"🆔 *Telegram ID:* `{message.from_user.id}`\n\n"
                f"📝 *Xabar:*\n{feedback}",
                parse_mode=ParseMode.MARKDOWN
            )
            delivered = True
        except Exception:
            log.exception("Fikr-mulohazani adminstratorga yuborib bo‘lmadi. admin_id=%s", admin_id)
    await state.clear()
    if delivered:
        await message.answer("✅ Fikr-mulohazangiz administratorga yuborildi. Rahmat!", reply_markup=MAIN_KEYBOARD)
    else:
        await message.answer("⚠️ Fikr-mulohaza yuborilmadi. Administrator sozlamalarida muammo bor.", reply_markup=MAIN_KEYBOARD)

# --- 1. MATN HANDLER ---
@dp.message(F.text)
async def text_handler(message: types.Message):
    if not message.text:
        return
    part = genai_types.Part(text=message.text)
    await process(message, parts=[part], store_parts=[part])

# --- 2. RASM HANDLER ---
@dp.message(F.photo)
async def photo_handler(message: types.Message):
    try:
        photo = message.photo[-1]
        file_data = await download_media(photo.file_id, bot)
        caption = message.caption or "Bu rasmda nima tasvirlanganini aytib bering."
        
        image_part = genai_types.Part.from_bytes(data=file_data, mime_type="image/jpeg")
        text_part = genai_types.Part(text=caption)
        history_part = genai_types.Part(text=f"[Rasm yuborildi] {caption}")
        
        await process(message, parts=[image_part, text_part], store_parts=[history_part])
    except Exception as exc:
        await message.answer(error_message(exc))

# --- 3. HUJJAT HANDLER (PDF, DOC, TXT) ---
@dp.message(F.document)
async def document_handler(message: types.Message):
    try:
        doc = message.document
        file_data = await download_media(doc.file_id, bot)
        mime_type = doc.mime_type or "application/octet-stream"
        caption = message.caption or "Ushbu hujjatni tahlil qilib, mazmunini aytib bering."
        
        doc_part = genai_types.Part.from_bytes(data=file_data, mime_type=mime_type)
        text_part = genai_types.Part(text=caption)
        history_part = genai_types.Part(text=f"[Hujjat yuborildi: {doc.file_name}] {caption}")
        
        await process(message, parts=[doc_part, text_part], store_parts=[history_part])
    except Exception as exc:
        await message.answer(error_message(exc))

# --- 4. VIDEO & GIF HANDLER ---
@dp.message(F.video | F.video_note | F.animation)
async def video_handler(message: types.Message):
    try:
        media = message.video or message.video_note or message.animation
        file_data = await download_media(media.file_id, bot)
        
        # Dumaloq videolar uchun mime_type bot orqali "video/mp4" deb taxmin qilinadi
        mime_type = getattr(media, 'mime_type', 'video/mp4') 
        caption = message.caption or "Ushbu videoni/animatsiyani tahlil qilib bering."
        
        video_part = genai_types.Part.from_bytes(data=file_data, mime_type=mime_type)
        text_part = genai_types.Part(text=caption)
        history_part = genai_types.Part(text=f"[Video yuborildi] {caption}")
        
        await process(message, parts=[video_part, text_part], store_parts=[history_part])
    except Exception as exc:
        await message.answer(error_message(exc))

# --- 5. AUDIO & OVOZLI XABAR (VOICE) HANDLER ---
@dp.message(F.audio | F.voice)
async def audio_handler(message: types.Message):
    try:
        media = message.audio or message.voice
        file_data = await download_media(media.file_id, bot)
        
        # Ovozli xabarlar uchun mime_type odatda "audio/ogg" bo'ladi
        mime_type = getattr(media, 'mime_type', 'audio/ogg')
        caption = message.caption or "Ushbu audio yozuvni eshitib, unga javob yozing yoki mazmunini aytib bering."
        
        audio_part = genai_types.Part.from_bytes(data=file_data, mime_type=mime_type)
        text_part = genai_types.Part(text=caption)
        history_part = genai_types.Part(text=f"[Audio yuborildi] {caption}")

        await process(message, parts=[audio_part, text_part], store_parts=[history_part])
    except Exception as exc:
        await message.answer(error_message(exc))

# --- BOSHQA TURDAGI XABARLAR ---
@dp.message()
async def other_handler(message: types.Message):
    await message.answer("Iltimos, matn, rasm, video, audio yoki hujjat yuboring.", reply_markup=MAIN_KEYBOARD)

async def main():
    if REQUIRE_ADMIN_APPROVAL and not ADMIN_USER_IDS:
        log.warning("ADMIN_USER_IDS bo‘sh va REQUIRE_ADMIN_APPROVAL True! Bot hammaga ochiq bo'lmaydi.")
    
    await bot.set_my_commands(
        [
            types.BotCommand(command="start", description="Asosiy menyu"),
            types.BotCommand(command="reset", description="Suhbatni tozalash"),
            types.BotCommand(command="myid", description="Telegram ID raqamim"),
            types.BotCommand(command="help", description="Yordam va qo'llanma"),
            types.BotCommand(command="cancel", description="Amalni bekor qilish"),
        ],
        scope=types.BotCommandScopeDefault()
    )
    
    log.info("Bot ishga tushdi. Model: %s | Admin ruxsati talabi: %s", MODEL, REQUIRE_ADMIN_APPROVAL)
    
    try:
        await dp.start_polling(bot)
    finally:
        await client.aio.aclose()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to‘xtatildi.")