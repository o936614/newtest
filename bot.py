# بوت تليجرام للتعامل مع المحتوى المقيد وقياس سرعة الإنترنت
# يستخدم مكتبة Pyrogram مع تحسينات للأداء، معالجة الأخطاء، إدارة الموارد، والأمان

import pyrogram
from pyrogram import Client, filters
from pyrogram.errors import UserAlreadyParticipant, InviteHashExpired, UsernameNotOccupied, FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import os
import json
import logging
import speedtest
import asyncio
from cachetools import TTLCache
from concurrent.futures import ThreadPoolExecutor
from tenacity import retry, stop_after_attempt, wait_fixed
from aiolimiter import AsyncLimiter
import tempfile
import re

# إعداد التسجيل (Logging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# إعداد التخزين المؤقت
cache = TTLCache(maxsize=100, ttl=3600)  # ذاكرة تخزين مؤقتة لمدة ساعة

# إعداد حوض الخيوط
executor = ThreadPoolExecutor(max_workers=5)

# إعداد الحد الأقصى للطلبات لتجنب الحظر
limiter = AsyncLimiter(20, 60)

# دالة لتحميل الإعدادات من ملف config.json أو متغيرات البيئة
def getenv(var):
    return os.environ.get(var) or DATA.get(var, None)

# تحميل الإعدادات
with open('config.json', 'r') as f:
    DATA = json.load(f)

bot_token = getenv("TOKEN")
api_hash = getenv("HASH")
api_id = getenv("ID")

# إنشاء عميل البوت
bot = Client("mybot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# إنشاء عميل المستخدم (اختياري)
ss = getenv("STRING")
if ss is not None:
    acc = Client("myacc", api_id=api_id, api_hash=api_hash, session_string=ss)
    acc.start()
    logger.info("User session started successfully.")
else:
    acc = None
    logger.warning("String session not set; private content may not be accessible.")

# دالة لجلب الرسائل مع التخزين المؤقت
async def get_cached_message(client, chatid, msgid):
    key = f"{chatid}:{msgid}"
    if key in cache:
        logger.info(f"Retrieved message {msgid} from cache")
        return cache[key]
    msg = await client.get_messages(chatid, msgid)
    cache[key] = msg
    logger.info(f"Cached message {msgid} for chat {chatid}")
    return msg

# دالة لإدارة التأخير لتجنب الحظر
async def rate_limited_sleep():
    async with limiter:
        await asyncio.sleep(0.1)

# دالة للتحقق من صحة روابط تليجرام
def is_valid_telegram_link(link):
    # نمط regex لدعم روابط الرسائل (عامة وخاصة) وروابط الدعوة
    pattern = r'^https://t\.me/(c/\d+/\d+|b/\w+/\d+|\+\w+|\w+/\d+)$'
    return bool(re.match(pattern, link))

# دالة للتحقق من إذن الوصول
async def check_chat_access(client, chatid):
    try:
        chat = await client.get_chat(chatid)
        logger.info(f"Access verified for chat {chatid}: {chat.title}")
        return True
    except Exception as e:
        logger.error(f"Cannot access chat {chatid}: {str(e)}")
        return False

# دالة لتحميل الوسائط مع التدفق وإعادة المحاولة
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
async def download_media_stream(client, message, progress_callback, progress_args):
    logger.info(f"Downloading media for message {message.id}")
    return await client.download_media(message, in_memory=False, progress=progress_callback, progress_args=progress_args)

# دالة للانضمام إلى الدردشات مع معالجة FloodWait
async def join_chat_with_retry(client, link):
    try:
        await client.join_chat(link)
        logger.info(f"Joined chat via link: {link}")
        return True
    except FloodWait as e:
        logger.warning(f"FloodWait error, waiting for {e.x} seconds")
        await asyncio.sleep(e.x)
        return False
    except Exception as e:
        logger.error(f"Error joining chat: {e}")
        raise

# دالة لتحديث حالة التحميل
async def downstatus(statusfile, message):
    while not os.path.exists(statusfile):
        await asyncio.sleep(1)
    await asyncio.sleep(3)
    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r") as downread:
                txt = downread.read()
            await bot.edit_message_text(message.chat.id, message.id, f"Downloaded: **{txt}**")
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Error editing download status message: {e}")
            await asyncio.sleep(5)

# دالة لتحديث حالة الرفع
async def upstatus(statusfile, message):
    while not os.path.exists(statusfile):
        await asyncio.sleep(1)
    await asyncio.sleep(3)
    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r") as upread:
                txt = upread.read()
            await bot.edit_message_text(message.chat.id, message.id, f"Uploaded: **{txt}**")
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Error editing upload status message: {e}")
            await asyncio.sleep(5)

# دالة لكتابة تقدم التحميل/الرفع إلى ملف مؤقت
def progress(current, total, message, type):
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"{type}status.txt") as fileup:
        fileup.write(f"{current * 100 / total:.1f}%".encode())
        return fileup.name

# دالة لقياس سرعة الإنترنت
async def speedtest_command(client, message):
    try:
        await message.reply_text("جاري قياس سرعة الانترنت، يرجى الانتظار...")
        st = speedtest.Speedtest()
        st.get_best_server()
        download_speed = st.download() / 1_000_000  # ميجابت بالثانية
        upload_speed = st.upload() / 1_000_000      # ميجابت بالثانية
        ping = st.results.ping

        result_message = (
            f"سرعة التحميل: {download_speed:.2f} Mbps\n"
            f"سرعة الرفع: {upload_speed:.2f} Mbps\n"
            f"البينغ: {ping:.2f} ms"
        )
        await message.reply_text(result_message)
        logger.info(f"Speedtest completed for user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Speedtest error: {e}")
        await message.reply_text(f"Sorry, an error occurred while measuring speed: {str(e)}. Please try again.")

# دالة لمعالجة مجموعات الوسائط
async def handle_media_group(client, message, chatid, msgid):
    try:
        media_group = await client.get_media_group(chatid, msgid)
        for media in media_group:
            await handle_private(message, chatid, media.id)
        logger.info(f"Processed media group {msgid} from chat {chatid}")
    except Exception as e:
        logger.error(f"Error handling media group: {e}")
        await bot.send_message(message.chat.id, f"Sorry, an error occurred while processing media group: {str(e)}. Please try again.")

# معالج أمر /start
@bot.on_message(filters.command(["start"]))
async def send_start(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
    logger.info(f"/start command received from user {message.from_user.id} ({message.from_user.first_name})")
    await bot.send_message(
        message.chat.id,
        f"👋 Hi **{message.from_user.mention}**, I am Save Restricted Bot, I can send you restricted content by its post link"
    )

# معالج أمر /speedtest
@bot.on_message(filters.command(["speedtest"]))
async def speedtest_handler(client, message):
    logger.info(f"/speedtest command received from user {message.from_user.id} ({message.from_user.first_name})")
    await speedtest_command(client, message)

# معالج الرسائل النصية (روابط تليجرام)
@bot.on_message(filters.text)
async def save(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
    logger.info(f"Text message received from {message.from_user.id}: {message.text}")

    # التحقق من صحة الرابط
    if not is_valid_telegram_link(message.text):
        await bot.send_message(message.chat.id, "Invalid Telegram link format. Please provide a valid Telegram link.")
        return

    # معالجة روابط الانضمام
    if "https://t.me/+" in message.text or "https://t.me/joinchat/" in message.text:
        if acc is None:
            await bot.send_message(
                message.chat.id,
                "Sorry, the string session is not set. Please configure it to access private content.",
                reply_to_message_id=message.id
            )
            logger.warning("Attempt to join chat without string session set.")
            return
        try:
            if await join_chat_with_retry(acc, message.text):
                await bot.send_message(message.chat.id, "**Chat Joined**", reply_to_message_id=message.id)
            else:
                await bot.send_message(message.chat.id, "Please wait and try again due to Telegram rate limits.", reply_to_message_id=message.id)
        except UserAlreadyParticipant:
            await bot.send_message(message.chat.id, "**Chat already Joined**", reply_to_message_id=message.id)
            logger.info("User already participant in chat.")
        except InviteHashExpired:
            await bot.send_message(message.chat.id, "**Invalid Link**", reply_to_message_id=message.id)
            logger.warning("Invite link expired or invalid.")
        except Exception as e:
            await bot.send_message(
                message.chat.id,
                f"Sorry, an error occurred while joining chat: {str(e)}. Please try again.",
                reply_to_message_id=message.id
            )
            logger.error(f"Error joining chat: {e}")
        return

    # معالجة روابط الرسائل
    if "https://t.me/" in message.text:
        datas = message.text.split("/")
        temp = datas[-1].replace("?single", "").split("-")
        fromID = int(temp[0].strip())
        try:
            toID = int(temp[1].strip())
        except IndexError:
            toID = fromID

        for msgid in range(fromID, toID + 1):
            # القنوات/المجموعات الخاصة
            if "https://t.me/c/" in message.text:
                chatid = int("-100" + datas[4])
                if acc is None:
                    await bot.send_message(
                        message.chat.id,
                        "Sorry, the string session is not set. Please configure it to access private content.",
                        reply_to_message_id=message.id
                    )
                    logger.warning("Attempt to access private chat without string session.")
                    return
                await handle_private(message, chatid, msgid)

            # البوتات
            elif "https://t.me/b/" in message.text:
                username = datas[4]
                if acc is None:
                    await bot.send_message(
                        message.chat.id,
                        "Sorry, the string session is not set. Please configure it to access private content.",
                        reply_to_message_id=message.id
                    )
                    logger.warning("Attempt to access bot content without string session.")
                    return
                try:
                    await handle_private(message, username, msgid)
                except Exception as e:
                    await bot.send_message(
                        message.chat.id,
                        f"Sorry, an error occurred while processing bot message: {str(e)}. Please try again.",
                        reply_to_message_id=message.id
                    )
                    logger.error(f"Error handling bot private message: {e}")

            # القنوات/المجموعات العامة
            else:
                username = datas[3]
                try:
                    msg = await get_cached_message(bot, username, msgid)
                except UsernameNotOccupied:
                    await bot.send_message(
                        message.chat.id,
                        "The provided username does not exist. Please check the link and try again.",
                        reply_to_message_id=message.id
                    )
                    logger.warning(f"Username not occupied: {username}")
                    return
                try:
                    if msg.media_group_id:
                        await handle_media_group(bot, message, msg.chat.id, msg.id)
                    else:
                        await bot.copy_message(message.chat.id, msg.chat.id, msg.id, reply_to_message_id=message.id)
                    logger.info(f"Copied message {msgid} from {username} to chat {message.chat.id}")
                except Exception as e:
                    if acc is None:
                        await bot.send_message(
                            message.chat.id,
                            "Sorry, the string session is not set. Please configure it to access private content.",
                            reply_to_message_id=message.id
                        )
                        logger.warning("Attempt to copy restricted media without string session.")
                        return
                    try:
                        await handle_private(message, username, msgid)
                    except Exception as e:
                        await bot.send_message(
                            message.chat.id,
                            f"Sorry, an error occurred while processing message: {str(e)}. Please try again.",
                            reply_to_message_id=message.id
                        )
                        logger.error(f"Error handling private message fallback: {e}")

            await rate_limited_sleep()

# دالة لمعالجة الرسائل الخاصة
async def handle_private(message: pyrogram.types.messages_and_media.message.Message, chatid: int, msgid: int):
    start_time = time.time()
    logger.info(f"Handling private message {msgid} from chat {chatid} for user {message.from_user.id}")

    # التحقق من إذن الوصول
    if not await check_chat_access(acc, chatid):
        await bot.send_message(message.chat.id, f"Cannot access chat {chatid}. Please ensure the account is a member and has permission to view messages.", reply_to_message_id=message.id)
        return

    # جلب الرسالة من التخزين المؤقت
    try:
        msg: pyrogram.types.messages_and_media.message.Message = await get_cached_message(acc, chatid, msgid)
        logger.info(f"Successfully retrieved message {msgid} from chat {chatid}")
    except Exception as e:
        logger.error(f"Failed to retrieve message {msgid} from chat {chatid}: {str(e)}")
        await bot.send_message(message.chat.id, f"Failed to retrieve message: {str(e)}", reply_to_message_id=message.id)
        return

    msg_type = get_message_type(msg)

    # معالجة الرسائل النصية
    if msg_type == "Text":
        await bot.send_message(message.chat.id, msg.text, entities=msg.entities, reply_to_message_id=message.id)
        logger.info(f"Finished processing message {msgid} in {time.time() - start_time:.2f} seconds")
        return

    # إرسال رسالة مؤقتة للتحميل
    smsg = await bot.send_message(message.chat.id, "Downloading", reply_to_message_id=message.id)

    # بدء تحديث حالة التحميل
    executor.submit(lambda: asyncio.run(downstatus(f"{message.id}downstatus.txt", smsg)))

    # تحميل الوسائط
    try:
        file = await download_media_stream(acc, msg, progress, [message, "down"])
    except Exception as e:
        logger.error(f"Failed to download media for message {msgid}: {str(e)}")
        await bot.send_message(message.chat.id, f"Failed to download media: {str(e)}", reply_to_message_id=message.id)
        return

    # حذف ملف حالة التحميل
    try:
        os.remove(f"{message.id}downstatus.txt")
    except OSError as e:
        logger.error(f"Failed to delete file {f'{message.id}downstatus.txt'}: {e}")

    # بدء تحديث حالة الرفع
    executor.submit(lambda: asyncio.run(upstatus(f"{message.id}upstatus.txt", smsg)))

    try:
        # معالجة أنواع الوسائط المختلفة
        if msg_type == "Document":
            thumb = None
            try:
                thumb = await download_media_stream(acc, msg.document.thumbs[0], None, None)
            except Exception:
                pass
            await bot.send_document(
                message.chat.id, file, thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities,
                reply_to_message_id=message.id, progress=progress, progress_args=[message, "up"]
            )
            if thumb:
                try:
                    os.remove(thumb)
                except OSError as e:
                    logger.error(f"Failed to delete thumbnail {thumb}: {e}")

        elif msg_type == "Video":
            thumb = None
            try:
                thumb = await download_media_stream(acc, msg.video.thumbs[0], None, None)
            except Exception:
                pass
            await bot.send_video(
                message.chat.id, file, duration=msg.video.duration, width=msg.video.width, height=msg.video.height,
                thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id,
                progress=progress, progress_args=[message, "up"]
            )
            if thumb:
                try:
                    os.remove(thumb)
                except OSError as e:
                    logger.error(f"Failed to delete thumbnail {thumb}: {e}")

        elif msg_type == "Animation":
            await bot.send_animation(message.chat.id, file, reply_to_message_id=message.id)

        elif msg_type == "Sticker":
            await bot.send_sticker(message.chat.id, file, reply_to_message_id=message.id)

        elif msg_type == "Voice":
            await bot.send_voice(
                message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities,
                reply_to_message_id=message.id, progress=progress, progress_args=[message, "up"]
            )

        elif msg_type == "Audio":
            thumb = None
            try:
                thumb = await download_media_stream(acc, msg.audio.thumbs[0], None, None)
            except Exception:
                pass
            await bot.send_audio(
                message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities,
                reply_to_message_id=message.id, progress=progress, progress_args=[message, "up"]
            )
            if thumb:
                try:
                    os.remove(thumb)
                except OSError as e:
                    logger.error(f"Failed to delete thumbnail {thumb}: {e}")

        elif msg_type == "Photo":
            await bot.send_photo(
                message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities,
                reply_to_message_id=message.id
            )

    except Exception as e:
        logger.error(f"Error sending media for message {msgid}: {str(e)}")
        await bot.send_message(
            message.chat.id,
            f"Sorry, an error occurred while sending media: {str(e)}. Please try again.",
            reply_to_message_id=message.id
        )
    finally:
        # حذف ملف الوسائط
        try:
            os.remove(file)
        except OSError as e:
            logger.error(f"Failed to delete file {file}: {e}")

        # حذف ملف حالة الرفع
        try:
            if os.path.exists(f"{message.id}upstatus.txt"):
                os.remove(f"{message.id}upstatus.txt")
        except OSError as e:
            logger.error(f"Failed to delete file {f'{message.id}upstatus.txt'}: {e}")

        # حذف رسالة التحميل المؤقتة
        try:
            await bot.delete_messages(message.chat.id, [smsg.id])
        except Exception as e:
            logger.error(f"Error deleting temporary message: {e}")

        logger.info(f"Finished processing message {msgid} in {time.time() - start_time:.2f} seconds")

# دالة لتحديد نوع الرسالة
def get_message_type(msg: pyrogram.types.messages_and_media.message.Message):
    try:
        if msg.document and msg.document.file_id:
            return "Document"
    except:
        pass
    try:
        if msg.video and msg.video.file_id:
            return "Video"
    except:
        pass
    try:
        if msg.animation and msg.animation.file_id:
            return "Animation"
    except:
        pass
    try:
        if msg.sticker and msg.sticker.file_id:
            return "Sticker"
    except:
        pass
    try:
        if msg.voice and msg.voice.file_id:
            return "Voice"
    except:
        pass
    try:
        if msg.audio and msg.audio.file_id:
            return "Audio"
    except:
        pass
    try:
        if msg.photo and msg.photo.file_id:
            return "Photo"
    except:
        pass
    try:
        if msg.text:
            return "Text"
    except:
        pass
    return "Unknown"

# تشغيل البوت
if __name__ == "__main__":
    logger.info("Bot is starting...")
    try:
        bot.run()
    finally:
        if acc:
            acc.stop()
            logger.info("User session stopped.")
