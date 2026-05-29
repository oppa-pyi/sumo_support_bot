import asyncio
import logging
import os
import csv
from datetime import datetime

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
USE_WEBHOOK = URL is not None
logger.info(f"Group ID: {SUPPORT_GROUP_ID} - Mode: {'webhook' if USE_WEBHOOK else 'polling'}")


# Helper: Save customer data to CSV
def save_customer(user_id: int, username: str, first_name: str, last_name: str = ""):
    filename = "customers.csv"
    file_exists = os.path.isfile(filename)
    
    if file_exists:
        with open(filename, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("user_id") == str(user_id):
                    return
    
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "username", "first_name", "last_name", "first_seen"])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name,
            "last_name": last_name,
            "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    logger.info(f"Saved customer: {user_id} (@{username})")


# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_customer(user.id, user.username, user.first_name, user.last_name or "")
    
    msg = (
        "🤖 **Sumo Mobile မှ ကြိုဆိုပါတယ်**\n\n"
        "ကျနော်ကတော့ Support Bot ပါ။\n"
        "Admin များမှ ဖြေကြားနေပါတယ်။ **ခနစောင့်ပေးပါ။**\n\n"
        "📍 **SUMO Mobile Monywa**\n"
        "📞 **ဆက်သွယ်ရန်**\n"
        "📱 09 780 780 440 (Phone / Viber)\n\n"
        "💬 မေးစရာရှိရင် ဒီမှာပဲ ရေးခဲ့ပါ။"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# Forward customer message to admin group
async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    
    if not message or not message.text or message.text.startswith('/'):
        return
    
    # Save customer info
    save_customer(user.id, user.username, user.first_name, user.last_name or "")
    
    # Prepare admin message with ALL customer info
    admin_text = (
        f"📨 **New message from customer**\n\n"
        f"👤 **Name:** {user.full_name}\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"📛 **Username:** @{user.username if user.username else 'None'}\n"
        f"💬 **Message:**\n{message.text}\n\n"
        f"👇 **Click the button below to reply**"
    )
    
    # Create reply button
    keyboard = [[InlineKeyboardButton("✏️ Reply to this customer", callback_data=f"reply_{user.id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=admin_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        await update.message.reply_text("✅ သင့်မေးခွန်းကို Admin များထံ ပေးပို့လိုက်ပါပြီ။\nအဖြေရရှိရန် ခနစောင့်ပေးပါ။")
        logger.info(f"Forwarded from {user.id} to group")
    except Exception as e:
        logger.error(f"Forward error: {e}")
        await update.message.reply_text("❌ Failed to send message. Please try again later.")


# Handle reply button click
async def on_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Extract customer ID from callback_data ("reply_123456789")
    customer_id = int(query.data.split('_')[1])
    
    # Store in user_data for this admin
    context.user_data['reply_to'] = customer_id
    
    # Try to get customer info from CSV
    customer_name = "Customer"
    customer_username = ""
    if os.path.isfile("customers.csv"):
        with open("customers.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("user_id") == str(customer_id):
                    customer_name = row.get("first_name", "Customer")
                    customer_username = row.get("username", "")
                    break
    
    await query.edit_message_text(
        f"✏️ **Replying to:** {customer_name}\n"
        f"🆔 ID: `{customer_id}`\n"
        f"📛 @{customer_username if customer_username else 'No username'}\n\n"
        "**Type your response below.**\n"
        "The customer will receive it immediately.\n\n"
        "Type /cancel to cancel reply.",
        parse_mode=ParseMode.MARKDOWN
    )


# Handle admin's reply message in group
async def on_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if not message or not message.text:
        return
    
    # Check if we're in reply mode
    if 'reply_to' not in context.user_data:
        return
    
    # Cancel if admin types /cancel
    if message.text.startswith('/cancel'):
        context.user_data.pop('reply_to', None)
        await message.reply_text("❌ Reply cancelled.")
        return
    
    customer_id = context.user_data['reply_to']
    reply_text = message.text
    
    try:
        await context.bot.send_message(
            chat_id=customer_id,
            text=f"📨 **Admin Response:**\n{reply_text}",
            parse_mode=ParseMode.MARKDOWN
        )
        await message.reply_text(f"✅ Reply sent to customer `{customer_id}`")
        logger.info(f"Reply sent to {customer_id}")
        
        # Clear reply mode
        context.user_data.pop('reply_to', None)
        
    except Exception as e:
        logger.error(f"Reply error: {e}")
        await message.reply_text(f"❌ Failed to send. Customer may have blocked the bot. Error: {str(e)[:100]}")


# Command for admin to get customer info
async def customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /customerinfo [user_id]\nExample: /customerinfo 123456789")
        return
    
    customer_id = args[0]
    
    if not os.path.isfile("customers.csv"):
        await update.message.reply_text("No customer data found.")
        return
    
    with open("customers.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("user_id") == customer_id:
                msg = (
                    f"👤 **Customer Info**\n\n"
                    f"🆔 ID: `{row['user_id']}`\n"
                    f"📛 Username: @{row['username'] if row['username'] else 'None'}\n"
                    f"👤 Name: {row['first_name']} {row['last_name']}\n"
                    f"📅 First seen: {row['first_seen']}"
                )
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
                return
    
    await update.message.reply_text(f"Customer ID {customer_id} not found.")


# Command to list all customers (admin only)
async def list_customers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.isfile("customers.csv"):
        await update.message.reply_text("No customers yet.")
        return
    
    with open("customers.csv", "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    
    if not reader:
        await update.message.reply_text("No customers yet.")
        return
    
    # Show last 10 customers
    recent = reader[-10:]
    msg = "📋 **Recent Customers:**\n\n"
    for c in recent:
        msg += f"🆔 `{c['user_id']}` - @{c['username'] if c['username'] else 'None'} - {c['first_name']}\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def main():
    application = Application.builder().token(TOKEN).updater(None).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("customerinfo", customer_info))
    application.add_handler(CommandHandler("customers", list_customers))
    application.add_handler(CallbackQueryHandler(on_reply_button, pattern="^reply_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, forward_to_admin))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUP, on_admin_reply))
    
    if USE_WEBHOOK:
        logger.info(f"Starting webhook mode on {URL}")
        await application.bot.set_webhook(url=f"{URL}/telegram")
        
        async def webhook(request: Request) -> Response:
            data = await request.json()
            await application.update_queue.put(Update.de_json(data, application.bot))
            return Response()
        
        async def health(_: Request) -> PlainTextResponse:
            return PlainTextResponse("OK")
        
        starlette_app = Starlette(routes=[
            Route("/telegram", webhook, methods=["POST"]),
            Route("/healthcheck", health, methods=["GET"]),
        ])
        
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
            await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
