import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

key = os.getenv("OPENAI_API_KEY")

print("API KEY BOR:", bool(key))
print("API KEY UZUNLIGI:", len(key) if key else 0)

client = AsyncOpenAI(api_key=key)


async def test():
    try:
        response = await client.responses.create(
            model="gpt-5.5",
            input="Salom"
        )

        print("JAVOB:", response.output_text)

    except Exception as e:
        print("XATO TURI:", type(e).__name__)
        print("XATO:", str(e))


asyncio.run(test())