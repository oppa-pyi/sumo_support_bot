import asyncio
import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ContextTypes,
    filters,
    MessageHandler,
    CommandHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Define configuration constants
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))
TOKEN = os.environ.get("BOT_TOKEN")
SUPPORT_GROUP_ID = os.environ.get("SUPPORT_GROUP_ID")

# Validate required environment variables
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")
if not SUPPORT_GROUP_ID:
    raise ValueError("SUPPORT_GROUP_ID environment variable is required")

SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID)

# Determine mode: webhook if URL is provided, polling otherwise
USE_WEBHOOK = URL is not None
logger.info("Running in %s mode", "webhook" if USE_WEBHOOK else "polling")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message when /start is issued."""
    user = update.effective_user
    logger.info("User %s started the bot", user.username or user.id)
    
    welcome_message = (
        "🤖 **Sumo Mobile မှ ကြိုဆိုပါတယ်**\n\n"
        "ကျနော်ကတော့ Support Bot ပါ။\n"
        "Admin များမှ ဖြေကြားနေပါတယ်။ **ခနစောင့်ပေးပါ။**\n\n"
        "📢 **Channel များ Join ရန်**\n"
        "• [Telegram Channel](https://t.me/your_channel)\n"
        "• [Facebook Page](https://fb.com/your_page)\n\n"
        "📍 **ဆိုင်လိပ်စာ**\n"
        "[Your Shop Address Here]\n\n"
        "📞 **ဆက်သွယ်ရန်**\n"
        "[Your Phone Number Here]\n\n"
        "💬 မေးစရာရှိရင် ဒီမှာပဲ ရေးခဲ့ပါ။"
    )
    
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user messages to the admin support group."""
    user = update.effective_user
    message = update.message
    
    if not message or not message.text:
        return
    
    # Don't forward if it's a command
    if message.text and message.text.startswith('/'):
        return
    
    logger.info("Forwarding message from %s to admin group", user.username or user.id)
    
    # Create a nice formatted message for admins
    admin_message = (
        f"📨 **New message from customer**\n\n"
        f"👤 **User:** {user.full_name}\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"📝 **Message:**\n{message.text}\n\n"
        f"✏️ Reply to this message to respond to the customer."
    )
    
    # Forward to support group
    await context.bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        text=admin_message,
        parse_mode=ParseMode.MARKDOWN
    )


async def reply_to_customer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to customer when admin replies in the group."""
    message = update.message
    
    if not message or not message.reply_to_message:
        return
    
    # Check if the message we're replying to is from the bot
    reply_to_text = message.reply_to_message.text or ""
    if not reply_to_text.startswith("📨 **New message from customer**"):
        return
    
    # Extract user ID from the replied message
    import re
    match = re.search(r"🆔 \*\*User ID:\*\* `(\d+)`", reply_to_text)
    if not match:
        await message.reply_text("Could not identify the customer. Please use the reply feature properly.")
        return
    
    customer_id = int(match.group(1))
    admin_response = message.text
    
    if not admin_response:
        await message.reply_text("Please provide a response message.")
        return
    
    logger.info("Sending reply from admin to customer %s", customer_id)
    
    try:
        await context.bot.send_message(
            chat_id=customer_id,
            text=f"📨 **Admin Response:**\n{admin_response}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify in group that response was sent
        await message.reply_text(f"✅ Response sent to customer (ID: {customer_id})")
        
    except Exception as e:
        logger.error("Failed to send message to customer %s: %s", customer_id, e)
        await message.reply_text(f"❌ Failed to send response. Customer may have blocked the bot.\nError: {str(e)}")


async def main() -> None:
    """Set up PTB application and run in webhook or polling mode."""
    if USE_WEBHOOK:
        # Webhook mode for production (Render)
        logger.info("Starting webhook mode with URL: %s", URL)
        application = Application.builder().token(TOKEN).updater(None).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admin))
        
        # Set webhook
        await application.bot.set_webhook(url=f"{URL}/telegram")

        # Setup web server
        async def telegram(request: Request) -> Response:
            data = await request.json()
            await application.update_queue.put(Update.de_json(data, application.bot))
            return Response()

        async def health(_: Request) -> PlainTextResponse:
            return PlainTextResponse("Bot is running!")

        app = Starlette(
            routes=[
                Route("/telegram", telegram, methods=["POST"]),
                Route("/healthcheck", health, methods=["GET"]),
            ]
        )

        config = uvicorn.Config(app=app, port=PORT, host="0.0.0.0")
        server = uvicorn.Server(config)

        async with application:
            await application.start()
            await server.serve()
            await application.stop()
    else:
        # Polling mode for local development
        logger.info("Starting polling mode for local development")
        application = Application.builder().token(TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, forward_to_admin))
        application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUP, reply_to_customer))

        async with application:
            await application.start()
            await application.updater.start_polling()
            logger.info("Bot started! Send a message to test it.")

            # Keep running until interrupted
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            finally:
                await application.updater.stop()
                await application.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error("Bot failed to start: %s", e)
        raise