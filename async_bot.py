import asyncio
import logging
import os
import json

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ContextTypes,
    filters,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Configuration
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))
TOKEN = os.environ.get("BOT_TOKEN")
SUPPORT_GROUP_ID_STR = os.environ.get("SUPPORT_GROUP_ID")

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")
if not SUPPORT_GROUP_ID_STR:
    raise ValueError("SUPPORT_GROUP_ID environment variable is required")

SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_STR)
logger.info(f"Target group ID: {SUPPORT_GROUP_ID}")

USE_WEBHOOK = URL is not None
logger.info(f"Running in {'webhook' if USE_WEBHOOK else 'polling'} mode")

# Store mapping between customers and their messages
# In production, use a database. For small scale, this is fine.
user_message_map = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message when /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.username or user.id} started the bot")
    
    welcome_message = (
        "🤖 **Sumo Mobile မှ ကြိုဆိုပါတယ်**\n\n"
        "ကျနော်ကတော့ Support Bot ပါ။\n"
        "Admin များမှ ဖြေကြားနေပါတယ်။ **ခနစောင့်ပေးပါ။**\n\n"
        "📢 **Channel များ Join ရန်**\n"
        "• [Telegram Channel](https://t.me/sumo_mobile)\n"
        "• [Facebook Page](https://fb.com/sumomobile_mm)\n\n"
        "📍 **ဆိုင်လိပ်စာ**\n"
        "[🚩ရုံးကြီးလမ်း၊ ရုံးကြီးရပ်၊ မုံရွာမြို့]\n\n"
        "📞 **Phone :**\n"
        "[09780780440/ 09780780330]\n\n"
        "💬 မေးစရာရှိရင် ဒီမှာပဲ ရေးခဲ့ပါ။"
    )
    
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user messages to the admin support group."""
    user = update.effective_user
    message = update.message
    
    if not message or not message.text:
        return
    
    if message.text.startswith('/'):
        return
    
    logger.info(f"Message from {user.username or user.id}: {message.text[:50]}...")
    
    # Store mapping for this user
    if user.id not in user_message_map:
        user_message_map[user.id] = []
    user_message_map[user.id].append({
        "text": message.text,
        "message_id": message.message_id
    })
    
    # Create a unique callback data to identify this conversation
    callback_data = f"reply_{user.id}_{message.message_id}"
    
    # Create keyboard with reply button
    keyboard = [
        [InlineKeyboardButton("✏️ Reply to this customer", callback_data=callback_data)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_message = (
        f"📨 **New message from customer**\n\n"
        f"👤 **Name:** {user.full_name}\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"👤 **Username:** @{user.username if user.username else 'N/A'}\n"
        f"💬 **Message:**\n{message.text}\n\n"
        f"👇 **Click the button below to reply**"
    )
    
    try:
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=admin_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        logger.info(f"Forwarded message from {user.id} to group with reply button")
        
        await update.message.reply_text(
            "✅ သင့်မေးခွန်းကို Admin များထံ ပေးပို့လိုက်ပါပြီ။\n"
            "အဖြေရရှိရန် ခနစောင့်ပေးပါ။"
        )
        
    except Exception as e:
        logger.error(f"Failed to send to group: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:100]}")


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the reply button click - opens a prompt for admin to type reply."""
    query = update.callback_query
    await query.answer()
    
    # Extract user_id and message_id from callback_data
    # Format: reply_{user_id}_{message_id}
    parts = query.data.split('_')
    if len(parts) != 3:
        await query.edit_message_text("Invalid callback data.")
        return
    
    customer_id = int(parts[1])
    original_msg_id = int(parts[2])
    
    # Store in context.user_data temporarily
    context.user_data['reply_to_customer'] = customer_id
    context.user_data['reply_to_msg_id'] = original_msg_id
    
    # Ask admin to type their reply
    await query.edit_message_text(
        f"✏️ **Replying to customer ID:** `{customer_id}`\n\n"
        f"Please type your response below. The customer will receive it immediately.\n\n"
        f"_(Type /cancel to cancel)_",
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the actual reply message from admin after button click."""
    message = update.message
    
    # Check if we're in reply mode
    if 'reply_to_customer' not in context.user_data:
        return
    
    # Cancel if admin types /cancel
    if message.text and message.text.startswith('/cancel'):
        context.user_data.pop('reply_to_customer', None)
        context.user_data.pop('reply_to_msg_id', None)
        await message.reply_text("❌ Reply cancelled.")
        return
    
    customer_id = context.user_data['reply_to_customer']
    admin_response = message.text
    
    if not admin_response:
        await message.reply_text("Please type a response.")
        return
    
    logger.info(f"Admin replying to customer {customer_id}")
    
    try:
        # Send the reply to customer
        await context.bot.send_message(
            chat_id=customer_id,
            text=f"📨 **Admin Response:**\n{admin_response}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify in group that response was sent
        await message.reply_text(f"✅ Response sent to customer `{customer_id}`")
        logger.info(f"Reply sent to customer {customer_id}")
        
        # Clear the reply mode
        context.user_data.pop('reply_to_customer', None)
        context.user_data.pop('reply_to_msg_id', None)
        
    except Exception as e:
        logger.error(f"Failed to send reply to customer {customer_id}: {e}")
        await message.reply_text(
            f"❌ Failed to send response. Customer may have blocked the bot.\n"
            f"Error: {str(e)[:100]}"
        )


async def main() -> None:
    """Set up PTB application and run."""
    application = Application.builder().token(TOKEN).updater(None).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, 
        forward_to_admin
    ))
    application.add_handler(CallbackQueryHandler(handle_reply_button, pattern="^reply_"))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUP, 
        handle_admin_reply
    ))
    
    if USE_WEBHOOK:
        logger.info(f"Starting webhook mode with URL: {URL}")
        
        webhook_url = f"{URL}/telegram"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
        
        async def telegram_webhook(request: Request) -> Response:
            data = await request.json()
            await application.update_queue.put(Update.de_json(data, application.bot))
            return Response()
        
        async def health(_: Request) -> PlainTextResponse:
            return PlainTextResponse("Bot is running!")
        
        starlette_app = Starlette(
            routes=[
                Route("/telegram", telegram_webhook, methods=["POST"]),
                Route("/healthcheck", health, methods=["GET"]),
            ]
        )
        
        config = uvicorn.Config(app=starlette_app, port=PORT, host="0.0.0.0")
        server = uvicorn.Server(config)
        
        async with application:
            await application.start()
            await server.serve()
            await application.stop()
    else:
        logger.info("Starting polling mode")
        async with application:
            await application.start()
            await application.updater.start_polling()
            logger.info("Bot started in polling mode!")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        raise
