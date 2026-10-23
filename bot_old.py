import asyncio
import os
import requests

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN topilmadi!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "Salom! 👋\n"
        "Men lokal Qwen AI yordamchisiman.\n"
        "Savolingizni yuboring."
    )


@dp.message()
async def message_handler(message: types.Message):
    if not message.text:
        await message.answer("Iltimos, matnli xabar yuboring.")
        return

    # Foydalanuvchiga bot ishlayotganini ko'rsatish
    await message.answer("Savolingizni ko‘rib chiqyapman, kuting...")

    try:
        print("Telegram xabari:", message.text)

        response = await asyncio.to_thread(
            requests.post,
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Sen foydali AI yordamchisan. O'zbekcha savollarga o'zbekcha javob ber."
                    },
                    {
                        "role": "user",
                        "content": message.text
                    }
                ],
                "stream": False
            },
            timeout=300
        )

        print("Ollama HTTP kodi:", response.status_code)
        response.raise_for_status()

        data = response.json()
        answer = data.get("message", {}).get("content", "").strip()

        if not answer:
            answer = "AI bo‘sh javob qaytardi."

        print("AI JAVOBI:", repr(answer))

        for i in range(0, len(answer), 4000):
            await message.answer(answer[i:i + 4000])

    except requests.exceptions.Timeout:
        print("Xatolik: Ollama javob berishga juda ko'p vaqt sarfladi.")
        await message.answer("AI juda sekin javob bermoqda. Qayta urinib ko‘ring.")

    except Exception as e:
        print("XATOLIK:", repr(e))
        await message.answer("Xatolik yuz berdi. Bot oynasidagi xabarni tekshiring.")


async def main():
    print("Telegram bot ishga tushdi. Ollama modeli:", MODEL)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())