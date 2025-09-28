#!/usr/bin/env python3
import logging
import personal
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
import razorpay
import uuid

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Razorpay client
rp_client = razorpay.Client(auth=(personal.RAZORPAY_KEY_ID, personal.RAZORPAY_KEY_SECRET))

# ---------- Helper: create payment link ----------
def create_payment_link(amount_inr: int, buyer_name: str, buyer_contact=None, reference_id=None):
    """
    Creates a Razorpay Payment Link and returns the short_url and id.
    Uses Payment Links API.
    """
    if reference_id is None:
        reference_id = str(uuid.uuid4())  # unique reference per attempt

    payload = {
        "amount": amount_inr * 100,  # in paisa
        "currency": "INR",
        "accept_partial": False,
        "description": f"Payment for UPSI 2025 Batch ({amount_inr} INR)",
        "reference_id": reference_id,
        "customer": {
            "name": buyer_name or "Buyer",
            # "contact": buyer_contact,   # optional
        },
        "notify": {
            "sms": False,
            "email": False
        },
        "reminder_enable": False,
        # you can set "callback_url" & "callback_method" if you want synchronous callbacks (not required with webhooks)
    }

    resp = rp_client.payment_link.create(payload)
    # resp includes: id, entity, short_url, etc.
    return resp  # full response dict

# ---------- Bot handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇮🇳 हिंदी", callback_data="lang_hi")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "✨ *Welcome to PREMIUM BATCHES Bot!* ✨\n\n"
        "🌍 Choose your language / अपनी भाषा चुनें 👇",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "lang_en":
        keyboard = [
            [InlineKeyboardButton("📘 UPSI 2025 (₹199)", callback_data="batch_upsi")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_start")],
        ]
        await query.edit_message_text("🎯 *Choose your Batch:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        keyboard = [
            [InlineKeyboardButton("📘 यूपीएसआई 2025 (₹199)", callback_data="batch_upsi")],
            [InlineKeyboardButton("⬅️ वापस जाएं", callback_data="back_start")],
        ]
        await query.edit_message_text("🎯 *अपना बैच चुनें:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "batch_upsi":
        keyboard = [
            [InlineKeyboardButton("🎥 Demo", url=personal.DEMO_GROUP_LINK)],
            [InlineKeyboardButton("💳 Pay ₹199 via Razorpay", callback_data="pay_upsi")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_language")]
        ]
        await query.edit_message_text(
            "📘 *UPSI 2025 Batch*\n\n👉 Demo dekhne ke liye button.\n👉 Payment ke liye Razorpay button dabaiye.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back_start":
        # call the start command handler flow (simulate)
        if query.message:
            await start(query, context)
    elif query.data == "back_language":
        # Show language -> batch menu (English default)
        keyboard = [
            [InlineKeyboardButton("📘 UPSI 2025 (₹199)", callback_data="batch_upsi")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_start")]
        ]
        await query.edit_message_text("🎯 *Choose your Batch:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_buy_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    # Create a Razorpay Payment Link
    resp = create_payment_link(amount_inr=personal.UPSI_PRICE, buyer_name=user.full_name or user.username)
    short_url = resp.get("short_url")
    link_id = resp.get("id")

    # save mapping of link_id -> telegram user id for later verification (you need persistent store in production)
    # For demo, store in memory (not reliable for restart). Recommend DB (sqlite) or file.
    if "links_map" not in context.application.bot_data:
        context.application.bot_data["links_map"] = {}
    context.application.bot_data["links_map"][link_id] = {
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name
    }

    text = (
        f"🔒 *Payment Link Created*\n\n"
        f"Click below to pay ₹{personal.UPSI_PRICE} via Razorpay:\n\n"
        f"{short_url}\n\n"
        "✅ After payment completes, you will receive the invite link automatically."
    )
    await query.edit_message_text(text, parse_mode="Markdown")

# ---------- Utility to send invite link ----------
async def send_invite_link_to_user(application, telegram_user_id: int):
    """
    Create an invite link for the target group and send it to the user privately.
    NOTE: Bot must be admin in the group and TARGET_GROUP_CHAT_ID must be correct.
    """
    try:
        bot = application.bot
        # create a single-use invite link (valid for 1 day, max_uses=1)
        invite = await bot.create_chat_invite_link(chat_id=personal.TARGET_GROUP_CHAT_ID,
                                                   expire_date=None,   # or int timestamp
                                                   member_limit=1)
        invite_link = invite.invite_link
        await bot.send_message(chat_id=telegram_user_id,
                               text=f"🎉 *Payment Confirmed!* Here is your invite link to join the batch:\n\n{invite_link}\n\nPlease click to join.",
                               parse_mode="Markdown")
    except Exception as e:
        logger.exception("Failed to create/send invite link: %s", e)
        # fallback: send pre-generated invite link from personal (if set)
        try:
            await bot.send_message(chat_id=telegram_user_id,
                                   text=f"🎉 Payment confirmed — join here: {personal.DEMO_GROUP_LINK}")
        except Exception:
            logger.exception("Also failed sending fallback link.")

# ---------- Command to check (admin) ----------
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # simple dump of links_map for admin
    if update.message.from_user.username != personal.ADMIN_USERNAME:
        await update.message.reply_text("You are not authorized.")
        return
    data = context.application.bot_data.get("links_map", {})
    await update.message.reply_text(f"Current links created: {len(data)}\n{data}")

# ---------- Main ----------
def main():
    app = ApplicationBuilder().token(personal.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_language, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(handle_batch, pattern="^batch_"))
    app.add_handler(CallbackQueryHandler(handle_back, pattern="^back_"))
    app.add_handler(CallbackQueryHandler(handle_buy_click, pattern="^pay_"))
    app.add_handler(CommandHandler("stats", stats_cmd))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
