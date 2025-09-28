#!/usr/bin/env python3
from flask import Flask, request, abort
import personal
import hmac, hashlib, json, logging
import razorpay
import requests
import threading
from telegram import Bot
from telegram.ext import ApplicationBuilder

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram Bot for sending messages from webhook thread
bot = Bot(token=personal.BOT_TOKEN)

def verify_razorpay_signature(body_bytes, signature_header, webhook_secret):
    """
    Verify Razorpay webhook signature (X-Razorpay-Signature) per docs.
    """
    expected = hmac.new(webhook_secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

@app.route("/razorpay_webhook", methods=["POST"])
def razorpay_webhook():
    payload = request.get_data()  # raw bytes, required for signature verification
    signature = request.headers.get("X-Razorpay-Signature", "")

    # verify signature
    if not verify_razorpay_signature(payload, signature, personal.RAZORPAY_WEBHOOK_SECRET):
        logger.warning("Webhook signature verification failed.")
        return ("Signature verification failed", 400)

    event = request.json
    logger.info("Received webhook event: %s", event.get("event"))

    # Handle payment link paid / payment captured events
    # Common webhook events: payment_link.paid, payment.captured
    ev = event.get("event")
    if ev in ("payment_link.paid", "payment.captured"):
        # Extract payment info -> find link_id (if payment_link contains)
        payload_obj = event.get("payload", {})
        payment_entity = None
        # For payment_link.paid: payload -> payment -> entity
        if payload_obj.get("payment"):
            payment_entity = payload_obj["payment"].get("entity")
        # For payment.captured: payload->payment->entity as well
        if not payment_entity:
            logger.info("No payment entity found in webhook.")
            return ("No payment entity", 200)

        # When created via payment links, there may be "order" or "payment_link" reference
        # For safety, examine payment_entity for "link_id" or "order_id" or "notes"
        # The webhook payload for payment_link.paid contains "payment_link" also under payload
        link_id = None
        if payload_obj.get("payment_link") and payload_obj["payment_link"].get("entity"):
            link_id = payload_obj["payment_link"]["entity"].get("id")
        elif payment_entity.get("order_id"):
            link_id = payment_entity.get("order_id")
        else:
            # try other fields or notes
            link_id = payment_entity.get("id")  # fallback - not ideal

        # Now map link_id -> telegram user id using some storage.
        # If you used the in-memory map in bot.py, it exists only in bot process.
        # Safer: keep a small database (sqlite). For demo, we will call Telegram API to find by reference_id if you stored reference.
        # Here we'll assume you used payment link id stored earlier in bot_data via some persistence endpoint.
        # For a simple flow: create a small HTTP endpoint on bot or central storage; here we'll keep it simple:
        logger.info("Detected link_id: %s", link_id)
        # For demo: assume you included "reference_id" in payment link and stored mapping on your own server.
        # TODO: implement DB lookup here.

        # For simplicity, if you included "reference_id" and it encodes telegram_id, you can parse it.
        # Example: reference_id = "tg_123456789" -> extract id
        reference_id = None
        # try to find reference_id in payment_entity or in payment_link entity:
        if payment_entity.get("notes"):
            reference_id = payment_entity["notes"].get("reference_id")
        # else check payment_entity fields
        if not reference_id and payload_obj.get("payment_link") and payload_obj["payment_link"]["entity"].get("reference_id"):
            reference_id = payload_obj["payment_link"]["entity"].get("reference_id")

        logger.info("reference_id: %s", reference_id)

        # If reference_id encodes telegram id (recommended), extract it and send invite
        telegram_user_id = None
        if reference_id and reference_id.startswith("tg_"):
            try:
                telegram_user_id = int(reference_id.split("_", 1)[1])
            except:
                telegram_user_id = None

        # fallback: if you can't extract, you can look up by payment_entity['id'] in your DB
        if telegram_user_id:
            # create group invite link and send to user
            try:
                # create invite link via bot API
                invite = bot.create_chat_invite_link(chat_id=personal.TARGET_GROUP_CHAT_ID,
                                                     expire_date=None,
                                                     member_limit=1)
                invite_link = invite.invite_link
                bot.send_message(chat_id=telegram_user_id,
                                 text=f"🎉 *Payment Confirmed!* Here is your invite link to join the batch:\n\n{invite_link}\n\nPlease click to join.",
                                 parse_mode="Markdown")
                # notify admin
                bot.send_message(chat_id=f"@{personal.ADMIN_USERNAME}",
                                 text=f"✅ Payment received. User {telegram_user_id} invited with link.")
            except Exception as e:
                logger.exception("Error sending invite: %s", e)
        else:
            # no telegram id; notify admin to manually add / match payment
            bot.send_message(chat_id=f"@{personal.ADMIN_USERNAME}",
                             text=f"⚠️ Payment received but could not identify Telegram user. Payment info:\n{json.dumps(payment_entity)[:800]}")
    # respond to Razorpay
    return ("OK", 200)

if __name__ == "__main__":
    # Run Flask app (production: use gunicorn / uWSGI behind HTTPS)
    app.run(host="0.0.0.0", port=5000)
