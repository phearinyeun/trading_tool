import os
import requests
from dotenv import load_dotenv
import time

load_dotenv()
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = 3600
TOP_NEWS_COUNT = 3
HIGH_IMPACT_THRESHOLD = 10

def send_telegram_photo(caption: str, photo_url: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption,
        "photo": photo_url,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, data=payload, timeout=10)
        res.raise_for_status()
        print("✅ Photo message sent")
    except Exception as e:
        print("❌ Telegram sendPhoto error:", e)

def get_latest_news():
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_API_KEY}&filter=hot"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        return res.json().get("results", [])
    except Exception as e:
        print("❌ CryptoPanic fetch error:", e)
        return []

# Send plain text message (fallback if no image)
def send_telegram_message(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, data=payload, timeout=10)
        res.raise_for_status()
        print("✅ Text message sent")
    except Exception as e:
        print("❌ Telegram sendMessage error:", e)


def market_signal(votes):
    positive = votes.get("positive", 0)
    negative = votes.get("negative", 0)
    if positive > negative:
        return "🚀 Bullish ⚡️ (Crypto)"
    elif negative > positive:
        return "⚠️ Bearish"
    else:
        return "🔍 Neutral"

def asset_impact(title):
    title_lower = title.lower()
    impact = []
    if "btc" in title_lower or "bitcoin" in title_lower:
        impact.append("📈 BTC: Bullish")
    if "eth" in title_lower or "ethereum" in title_lower:
        impact.append("📈 ETH: Bullish")
    if any(word in title_lower for word in ["altcoin", "altcoins", "crypto market"]):
        impact.append("📊 Altcoins: Monitor")
    if not impact:
        impact.append("📊 General Market: Neutral")
    return "\n".join(impact)

def why_it_matters(title):
    title_lower = title.lower()
    if any(word in title_lower for word in ["surge", "rally", "rise", "gain", "increase"]):
        return "▸ Indicates rising investor confidence.\n▸ Could attract new buyers."
    elif any(word in title_lower for word in ["fall", "drop", "crash", "decline", "loss"]):
        return "▸ Indicates selling pressure or market fear.\n▸ Traders may shift to stable assets."
    elif any(word in title_lower for word in ["etf", "approval", "regulation", "ban"]):
        return "▸ Regulatory news can heavily impact prices.\n▸ Positive clarity may drive growth, delays or bans may trigger fear."
    elif any(word in title_lower for word in ["hack", "security", "breach", "exploit"]):
        return "▸ Security issues undermine investor trust.\n▸ Could cause short-term sell pressure on affected assets."
    elif any(word in title_lower for word in ["partnership", "expands", "launches", "adopts"]):
        return "▸ Shows ecosystem growth and adoption.\n▸ Market may view it as long-term bullish."
    else:
        return "▸ Could influence overall market sentiment."

def format_news_with_image(post):
    title = post.get("title", "No title")
    domain = post.get("domain", "")
    url_link = post.get("url", "")
    votes = post.get("votes", {})
    positive = votes.get("positive", 0)
    negative = votes.get("negative", 0)
    important = votes.get("important", 0)
    image_url = post.get("image")  # CryptoPanic image field

    signal = market_signal(votes)
    impact = asset_impact(title)
    reasoning = why_it_matters(title)

    high_impact = ""
    if positive + negative >= HIGH_IMPACT_THRESHOLD:
        high_impact = "🚨 *High Impact News!* 🚨\n"

    caption = (
        f"{high_impact}📉 *Breaking News:* {title}\n\n"
        f"📊 *What happened:*\n"
        f"▸ Source: {domain}\n"
        f"▸ Votes → 👍 {positive} | 👎 {negative} | ⚡ Important: {important}\n\n"
        f"💡 *Why it matters:*\n{reasoning}\n\n"
        f"📊 *Predicted Asset Impact:*\n{impact}\n\n"
        f"📈 *Market Signals:* {signal}\n"
        f"🔗 [Read more]({url_link})"
    )
    return caption, image_url


def run_cycle():
    posts = get_latest_news()
    if not posts:
        print("No news found")
        return

    top_posts = posts[:TOP_NEWS_COUNT]
    for post in top_posts:
        caption, image_url = format_news_with_image(post)
        if image_url:
            send_telegram_photo(caption, image_url)
        else:
            # fallback to text message if no image
            send_telegram_message(caption)
        time.sleep(2)

if __name__ == "__main__":
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("❌ Error:", e)
        time.sleep(CHECK_INTERVAL)
