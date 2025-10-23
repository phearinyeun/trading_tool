import os
import requests
from dotenv import load_dotenv

# Load your bot token from .env
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN:
    print("❌ Please set TELEGRAM_BOT_TOKEN in your .env file")
    exit(1)

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

try:
    response = requests.get(url)
    data = response.json()

    if data["ok"]:
        updates = data.get("result", [])
        if not updates:
            print("ℹ️ No messages yet. Please send a message to your bot or in your group first.")
        else:
            print("✅ Found chat IDs:")
            for update in updates:
                if "message" in update:
                    chat = update["message"]["chat"]
                    print(f"- Chat ID: {chat['id']} | Type: {chat['type']} | Title/Name: {chat.get('title') or chat.get('first_name')}")
    else:
        print(f"❌ Error: {data}")

except Exception as e:
    print(f"❌ Request failed: {e}")
