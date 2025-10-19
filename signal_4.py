import os
import json
import time
from datetime import datetime, timedelta
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv
from requests.exceptions import RequestException
import requests

# ===========================
# 1️⃣ Load Environment Variables
# ===========================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", 0))
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

LAST_SIGNAL_FILE = "last_signals.json"
DAILY_STATS_FILE = "daily_stats.json"
KEY_INDICATORS = ["close", "open", "high", "low", "RSI", "MACD.macd", "MACD.signal"]

# ===========================
# 2️⃣ Helper Functions
# ===========================
def format_price(price):
    return round(price, 2) if price >= 1 else round(price, 5)

def safe_get(url, params=None, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response
        except RequestException as e:
            print(f"❌ Request failed ({attempt+1}/{retries}): {e}")
            time.sleep(delay)
    return None

def load_json_file(filename, default_data):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except:
            pass
    return default_data

def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

# ===========================
# 3️⃣ Symbols Configuration
# ===========================
DEFAULT_SYMBOLS = {
    "BTCUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
    "ETHUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
    "DOGEUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
}

def read_symbols_config():
    if os.path.exists("symbols_config.json"):
        try:
            with open("symbols_config.json", "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                elif isinstance(data, list):
                    converted = {}
                    for item in data:
                        symbol = item.get("symbol")
                        if symbol:
                            converted[symbol] = {
                                "sl_percent": item.get("sl_percent", 0.5),
                                "reward_ratio": item.get("reward_ratio", 2),
                            }
                    return converted
        except Exception as e:
            print(f"❌ Failed to read symbols_config.json: {e}")
    print("⚠️ symbols_config.json not found! Using default symbols.")
    return DEFAULT_SYMBOLS

# ===========================
# 4️⃣ Signal Generation
# ===========================
def generate_signal(symbol, analysis, current_time, config):
    signals = []
    recommendation = analysis.summary.get("RECOMMENDATION", "NEUTRAL")
    pattern_name = f"TradingView Signal: {recommendation}"
    close_price = float(analysis.indicators.get("close", 0))
    if close_price == 0:
        return signals

    sl_percent = config.get("sl_percent", 0.5)
    reward_ratio = config.get("reward_ratio", 2)

    if recommendation == "BUY":
        sl = close_price * (1 - sl_percent / 100)
        tp = close_price + (close_price - sl) * reward_ratio
        signals.append({
            "symbol": symbol,
            "time": current_time,
            "signal": "LONG",
            "entry": format_price(close_price),
            "sl": format_price(sl),
            "tp": format_price(tp),
            "pattern": pattern_name,
            "indicators": analysis.indicators,
        })
    elif recommendation == "SELL":
        sl = close_price * (1 + sl_percent / 100)
        tp = close_price - (sl - close_price) * reward_ratio
        signals.append({
            "symbol": symbol,
            "time": current_time,
            "signal": "SHORT",
            "entry": format_price(close_price),
            "sl": format_price(sl),
            "tp": format_price(tp),
            "pattern": pattern_name,
            "indicators": analysis.indicators,
        })
    return signals

# ===========================
# 5️⃣ Telegram Notification
# ===========================
def send_signal_to_telegram(signals):
    for sig in signals:
        indicators_text = "\n".join([
            f"- {k}: {format_price(sig['indicators'][k]) if k in sig['indicators'] else 'N/A'}"
            for k in KEY_INDICATORS
        ])
        message = (
            f"⏰ *{sig['time']}*\n"
            f"📌 *Symbol:* {sig['symbol']}\n"
            f"💹 *Signal:* {sig['signal']}\n"
            f"💰 *Entry:* `{sig['entry']}`\n"
            f"🛑 *Stop Loss:* `{sig['sl']}`\n"
            f"🎯 *Take Profit:* `{sig['tp']}`\n"
            f"🔹 *Pattern:* {sig['pattern']}\n\n"
            f"📊 *Key Indicators:*\n{indicators_text}"
        )
        safe_get(TELEGRAM_API_URL, {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })

# ===========================
# 6️⃣ Daily Stats
# ===========================
def update_daily_stats(signal_type):
    stats = load_json_file(DAILY_STATS_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in stats:
        stats[today] = {"LONG": 0, "SHORT": 0}
    stats[today][signal_type] += 1
    save_json_file(DAILY_STATS_FILE, stats)

def send_daily_summary():
    stats = load_json_file(DAILY_STATS_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    day_stats = stats.get(today, {"LONG": 0, "SHORT": 0})
    total = day_stats["LONG"] + day_stats["SHORT"]
    message = (
        f"📅 *Daily Signals Summary ({today})*\n"
        f"🟢 LONG: {day_stats['LONG']}\n"
        f"🔴 SHORT: {day_stats['SHORT']}\n"
        f"📊 Total: {total} signals sent today"
    )
    safe_get(TELEGRAM_API_URL, {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })

# ===========================
# 7️⃣ Main Loop
# ===========================
def main():
    print("🚀 TradingView 10-Minute Signal Bot Started...")
    last_signals = load_json_file(LAST_SIGNAL_FILE, {})
    if not isinstance(last_signals, dict):
        last_signals = {}
    last_summary_time = datetime.now()

    symbols_config = read_symbols_config()

    while True:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for symbol, config in symbols_config.items():
            try:
                handler = TA_Handler(
                    symbol=symbol,
                    screener="crypto",
                    exchange="BINANCE",  # Only for TradingView analysis
                    interval=Interval.INTERVAL_5_MINUTES
                )
                analysis = handler.get_analysis()
                signals = generate_signal(symbol, analysis, current_time, config)

                last_signal_entry = last_signals.get(symbol, {})
                last_signal_time = last_signal_entry.get("time") if isinstance(last_signal_entry, dict) else None

                if signals and (
                    not last_signal_time or
                    datetime.strptime(last_signal_time, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=10) <= datetime.now()
                ):
                    send_signal_to_telegram(signals)
                    for s in signals:
                        update_daily_stats(s["signal"])
                    last_signals[symbol] = {"signals": signals, "time": current_time}
                    save_json_file(LAST_SIGNAL_FILE, last_signals)
                    print(f"✅ Sent new signal for {symbol} at {current_time}")
                else:
                    print(f"ℹ️ No new signal for {symbol} at {current_time}")

            except Exception as e:
                print(f"⚠️ Error for {symbol}: {e}")

        if datetime.now() - last_summary_time >= timedelta(hours=24):
            send_daily_summary()
            last_summary_time = datetime.now()
            print("📤 Daily summary sent!")

        print("⏳ Waiting 10 minutes before next scan...\n")
        time.sleep(600)

if __name__ == "__main__":
    main()
