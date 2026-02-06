import os
import logging
import tempfile
import asyncio
import requests
import aiohttp
import shutil
import subprocess
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- Load ENV Vars (Set on Railway) ---
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "8322816910:AAGn4c-P_LWOpmYLDizEyBh0_mj8MoqBq7o")
SE_API        = os.getenv("SHRINKEARN_API_KEY", "99a63d500ee702637ffd27ec4207e249654e3ff6")
GOFILE_TOKEN  = os.getenv("GOFILE_TOKEN", "2wFdfqpRdzSy4SWs99PdhjHdHYuEQAxt")
GOFILE_FOLDER = os.getenv("GOFILE_FOLDER_ID", "f1233e99-86d3-4759-919d-512cec4b7109")

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
async def run_ffmpeg_preview(input_file, output_file):
    """
    Generate 5-second low quality preview (360p, 800kbps)
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-t", "5",  # 5 seconds
        "-vf", "scale=-2:360",  # height 360p, width auto
        "-b:v", "800k",         # bitrate
        "-preset", "ultrafast",
        "-an",
        output_file,
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg preview error: {stderr.decode()}")
        raise Exception("Error generating preview.")

async def run_ffmpeg_upscale(input_file, output_file):
    """
    Upscale video to 4K (3840x2160), libx264, ultrafast
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-vf", "scale=3840:2160",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "copy",  # preserve audio
        output_file,
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg upscale error: {stderr.decode()}")
        raise Exception("Error upscaling video.")

async def upload_to_gofile(file_path):
    """
    Uploads the file to GoFile.io.
    """
    upload_url = "https://api.gofile.io/uploadFile"
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {
            'token': GOFILE_TOKEN,
            'folderId': GOFILE_FOLDER,
        }
        # Use requests (synchronous call inside thread)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: requests.post(upload_url, files=files, data=data, timeout=60))
    result = resp.json()
    if result['status'] != 'ok':
        logger.error(f"GoFile upload failed: {result}")
        raise Exception("GoFile upload failed.")
    return result['data']['downloadPage']

async def monetise_url_with_shrinkearn(url):
    """
    Returns a monetized short URL from ShrinkEarn.com.
    """
    shorten_url = "https://shrinkearn.com/api"
    params = {
        "api": SE_API,
        "url": url,
        "alias": "",
        "format": "json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(shorten_url, params=params) as resp:
            data = await resp.json()
    if data['status'] != "success":
        logger.error(f"ShrinkEarn failed: {data}")
        raise Exception("ShrinkEarn failed.")
    return data['shortenedUrl']

# --- Handlers ---
async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    # Inform the user we're processing
    status_msg = await message.reply_text("⏳ Processing your video...")

    # --- Temporary Dir for Safe Handling ---
    tmp_dir = tempfile.mkdtemp()
    try:
        # 1. Download video
        video = message.video or message.document
        file_id = video.file_id
        file = await context.bot.get_file(file_id)
        input_path = os.path.join(tmp_dir, 'input.mp4')
        await file.download_to_drive(input_path)

        # 2. Generate 5s Preview
        preview_path = os.path.join(tmp_dir, 'preview.mp4')
        await run_ffmpeg_preview(input_path, preview_path)

        # 3. Upscale full video to 4K
        upscale_path = os.path.join(tmp_dir, 'upscaled.mp4')
        await run_ffmpeg_upscale(input_path, upscale_path)

        # 4. Upload 4K video to GoFile
        gofile_url = await upload_to_gofile(upscale_path)

        # 5. ShrinkEarn monetized link
        monetized_url = await monetise_url_with_shrinkearn(gofile_url)

        # 6. Send Preview with Button
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬇️ Download Full 4K Video", url=monetized_url)]
        ])
        with open(preview_path, "rb") as prev:
            await message.reply_video(
                video=prev,
                caption="🎞️ Here is your 5s 360p preview. Click below to download the Full 4K Video.",
                reply_markup=keyboard,
            )
        await status_msg.delete()
    except Exception as e:
        logger.exception("Error processing video")
        await message.reply_text("❌ Sorry, there was an error processing your video.")
    finally:
        # Clean up
        shutil.rmtree(tmp_dir, ignore_errors=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send me a video and I'll upscale it to 4K automatically!"
    )

# --- Main Entrypoint ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex("^/start$"), start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video))
    app.run_polling(allowed_updates=['message', 'edited_message'])

if __name__ == "__main__":
    main()
