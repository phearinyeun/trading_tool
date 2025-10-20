import os
import json
import time
import requests
from datetime import datetime, timedelta
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv
from requests.exceptions import RequestException

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
    if price >= 1:
        return round(price, 2)
    else:
        return round(price, 5)

def read_symbols_config(file_path="symbols_config.json"):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ symbols_config.json not found! Using default symbols.")
        return {
            "ETHUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
            "BTCUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
            "DOGEUSDT": {"sl_percent": 0.5, "reward_ratio": 2},
        }

def safe_get(url, params, retries=3, delay=5):
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
        with open(filename, "r") as f:
            return json.load(f)
    return default_data

def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def generate_signal_tv_json(symbol, analysis, current_time, sl_percent, reward_ratio):
    close_price = float(analysis.indicators.get("close", 0))
    signals = []

    recommendation = analysis.summary.get("RECOMMENDATION", "NEUTRAL")
    pattern_name = f"TradingView Signal: {recommendation}"

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

        # ✅ Inline buttons
        buttons = {
            "inline_keyboard": [
                [
                    {"text": "Acknowledge ✅", "callback_data": f"ack_{sig['symbol']}"},
                    {"text": "Generate New Signal 🔄", "callback_data": f"new_{sig['symbol']}"}
                ]
            ]
        }

        safe_get(TELEGRAM_API_URL, {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(buttons)
        })


def send_daily_summary():
    """Send a daily summary report to Telegram."""
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

def update_daily_stats(signal_type):
    """Increment LONG/SHORT counters per day."""
    stats = load_json_file(DAILY_STATS_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")

    if today not in stats:
        stats[today] = {"LONG": 0, "SHORT": 0}

    stats[today][signal_type] += 1
    save_json_file(DAILY_STATS_FILE, stats)

def log_signals_to_file(signals, filename="signals_log.jsonl"):
    with open(filename, "a") as f:
        for sig in signals:
            f.write(json.dumps(sig) + "\n")

# ===========================
# 3️⃣ Continuous Bot Loop
# ===========================
def main():
    print("🚀 TradingView Signal Bot Started...")
    last_signals = load_json_file(LAST_SIGNAL_FILE, {})
    last_summary_time = datetime.now()

    while True:
        symbols_config = read_symbols_config()

        for symbol, config in symbols_config.items():
            try:
                handler = TA_Handler(
                    symbol=symbol,
                    screener="crypto",
                    exchange="BINANCE",
                    interval=Interval.INTERVAL_1_HOUR
                )
                analysis = handler.get_analysis()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                signals = generate_signal_tv_json(
                    symbol, analysis, current_time,
                    sl_percent=config.get("sl_percent", 0.5),
                    reward_ratio=config.get("reward_ratio", 2)
                )

                if signals and signals != last_signals.get(symbol):
                    send_signal_to_telegram(signals)
                    log_signals_to_file(signals)
                    for s in signals:
                        update_daily_stats(s["signal"])

                    last_signals[symbol] = signals
                    save_json_file(LAST_SIGNAL_FILE, last_signals)
                    print(f"✅ Sent new signal for {symbol} at {current_time}")
                else:
                    print(f"ℹ️ No new signal for {symbol} at {current_time}")

            except Exception as e:
                print(f"⚠️ Error for {symbol}: {e}")

        # 📅 Send daily summary every 24 hours
        if datetime.now() - last_summary_time >= timedelta(hours=24):
            send_daily_summary()
            last_summary_time = datetime.now()
            print("📤 Daily summary sent!")

        print("⏳ Waiting 1 hour before next scan...\n")
        time.sleep(3600)

if __name__ == "__main__":
    main()
