import os
import requests

# 1️⃣ Set your bot token here or in your .env
BOT_TOKEN = "8277391862:AAHaIJt5Kr5y_DryP2bM0jqZXYC9015OdGI"
URL = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

# 2️⃣ Fetch updates from the bot
response = requests.get(URL).json()

if "result" not in response or len(response["result"]) == 0:
    print("No updates found. Send a message in your group first!")
else:
    # Go through all updates
    for update in response["result"]:
        if "message" in update and "chat" in update["message"]:
            chat = update["message"]["chat"]
            chat_id = chat["id"]
            chat_title = chat.get("title", "Private Chat")
            print(f"Chat Title: {chat_title}")
            print(f"CHAT_ID: {chat_id}\n")
