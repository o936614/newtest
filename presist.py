import asyncio
import logging
from telegram import Bot

TOKEN = "7561938787:AAHYJQdi4nC4BeVU2BolAVO7-gpsIZCna0g"
CHAT_ID = "7151311758"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

async def send_message_every_5_minutes():
    bot = Bot(token=TOKEN)
    count = 1
    while True:
        message_text = f"Reactive Server\n\nThis is message number #{count}\n\nServer status : Active ✅"
        await bot.send_message(chat_id=CHAT_ID, text=message_text)
        logging.info(f"Sent: {message_text}")
        count += 1
        await asyncio.sleep(300)

asyncio.run(send_message_every_5_minutes())
