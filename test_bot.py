"""
test_bot.py
-----------
Diagnostic test script to verify Telegram credentials and channel permissions.
"""

import asyncio
import os
import sys
import telegram
import telegram.error
import config

BOT_TOKEN = config.BOT_TOKEN
CHANNEL_USERNAME = config.PRIVATE_REVIEW_CHANNEL

async def main():
    print("=" * 60)
    print("🧪 TELEGRAM BOT CONNECTION TEST SCRIPT 🧪")
    print("=" * 60)

    if not config.is_configured():
        print("❌ Error: TELEGRAM_BOT_TOKEN or TELEGRAM_PRIVATE_REVIEW_CHANNEL not configured in .env!")
        return

    token = BOT_TOKEN.strip().replace(" ", "")
    masked_token = f"{token[:10]}...{token[-5:]}" if len(token) > 15 else token
    print(f"🤖 Bot Token Loaded: {masked_token}")
    print(f"📢 Target Channel: {CHANNEL_USERNAME}")
    print("\nAttempting to connect to Telegram and send a message...")

    try:
        bot = telegram.Bot(token=token)
        bot_info = await bot.get_me()
        print(f"✅ Connection successful!")
        print(f"🤖 Bot Name: {bot_info.first_name}")
        print(f"🤖 Bot Username: @{bot_info.username}")

        test_message = (
            "🤖 <b>Bot Connection Test</b>\n\n"
            "This is a test message to verify credentials and channel permissions for VPS 24/7 deployment."
        )

        print(f"📨 Sending test message to {CHANNEL_USERNAME}...")
        sent_message = await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=test_message,
            parse_mode="HTML"
        )
        print("🎉 SUCCESS! The message was sent successfully to your channel.")
        print(f"💬 Message ID: {sent_message.message_id}")

    except telegram.error.InvalidToken:
        print("❌ Error: The Telegram Bot Token is invalid! Check .env file.")
    except telegram.error.BadRequest as e:
        print(f"❌ Error: Bad Request - {e}")
        print("   Make sure the bot is an Administrator in the channel with posting permissions!")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
