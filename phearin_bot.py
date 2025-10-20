import os
import json
import time
from datetime import datetime, timedelta
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv
import requests

# ===========================
# 1️⃣ Load Environment Variables
# ===========================
load_dotenv()
# TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_TOKEN = "8360975822:AAEeUFXicOQg9IvrExiNHEYyXRYdEkyiUK4"
# CHAT_ID = int(os.getenv("CHAT_ID", 0))
CHAT_ID = -1003115708912
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

LAST_SIGNAL_FILE = "last_signals.json"
DAILY_STATS_FILE = "daily_stats.json"
KEY_INDICATORS = ["close", "open", "high", "low", "RSI", "MACD.macd", "MACD.signal"]

# ===========================
# 2️⃣ Helper Functions
# ===========================
def format_price(price):
    return round(price, 2) if price >= 1 else round(price, 5)

def load_json_file(filename, default_data=None):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"❌ JSON decode error in {filename}. Resetting file.")
            return default_data if default_data else {}
    return default_data if default_data else {}

def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def safe_get(url, params, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response
        except requests.RequestException as e:
            print(f"❌ Request failed ({attempt+1}/{retries}): {e}")
            time.sleep(delay)
    return None

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

# ===========================
# 3️⃣ Candlestick Pattern Detection
# ===========================
def is_bullish(candle):
    return candle["close"] > candle["open"]

def is_bearish(candle):
    return candle["close"] < candle["open"]

def body_size(candle):
    return abs(candle["close"] - candle["open"])

def shadow_size(candle):
    lower = candle["open"] - candle["low"] if is_bullish(candle) else candle["close"] - candle["low"]
    upper = candle["high"] - candle["close"] if is_bullish(candle) else candle["high"] - candle["open"]
    return lower, upper

def detect_candlestick_pattern(candles):
    """
    Detects single, two, and three-candle patterns.
    candles: list of dicts with 'open', 'close', 'high', 'low'
    Returns: signal_type (BUY/SELL/NEUTRAL), pattern_name
    """
    if len(candles) < 3:
        return "NEUTRAL", "Insufficient data"

    prev2, prev1, curr = candles[-3], candles[-2], candles[-1]

    # --- Single Candle Patterns ---
    lower, upper = shadow_size(curr)
    bsize = body_size(curr)
    candle_range = curr["high"] - curr["low"] + 1e-5

    # Doji
    if bsize / candle_range < 0.1:
        return "NEUTRAL", "Doji"

    # Hammer
    if lower >= 2*bsize and upper <= bsize:
        return "BUY", "Hammer"

    # Hanging Man (bearish hammer at top)
    if lower >= 2*bsize and upper <= bsize and is_bearish(curr):
        return "SELL", "Hanging Man"

    # Shooting Star
    if upper >= 2*bsize and lower <= bsize:
        return "SELL", "Shooting Star"

    # Inverted Hammer
    if upper >= 2*bsize and lower <= bsize and is_bullish(curr):
        return "BUY", "Inverted Hammer"

    # --- Two Candle Patterns ---
    # Bullish Engulfing
    if is_bearish(prev1) and is_bullish(curr) and curr["close"] > prev1["open"] and curr["open"] < prev1["close"]:
        return "BUY", "Bullish Engulfing"

    # Bearish Engulfing
    if is_bullish(prev1) and is_bearish(curr) and curr["open"] > prev1["close"] and curr["close"] < prev1["open"]:
        return "SELL", "Bearish Engulfing"

    # Tweezer Bottom
    if is_bearish(prev1) and is_bullish(curr) and prev1["low"] == curr["low"]:
        return "BUY", "Tweezer Bottom"

    # Tweezer Top
    if is_bullish(prev1) and is_bearish(curr) and prev1["high"] == curr["high"]:
        return "SELL", "Tweezer Top"

    # Piercing Line
    if is_bearish(prev1) and is_bullish(curr) and curr["close"] > (prev1["open"] + prev1["close"])/2 and curr["open"] < prev1["close"]:
        return "BUY", "Piercing Line"

    # Dark Cloud Cover
    if is_bullish(prev1) and is_bearish(curr) and curr["close"] < (prev1["open"] + prev1["close"])/2 and curr["open"] > prev1["close"]:
        return "SELL", "Dark Cloud Cover"

    # --- Three Candle Patterns ---
    # Morning Star
    if is_bearish(prev2) and body_size(prev1) / body_size(prev2) < 0.5 and is_bullish(curr) and curr["close"] > (prev2["open"] + prev2["close"])/2:
        return "BUY", "Morning Star"

    # Evening Star
    if is_bullish(prev2) and body_size(prev1) / body_size(prev2) < 0.5 and is_bearish(curr) and curr["close"] < (prev2["open"] + prev2["close"])/2:
        return "SELL", "Evening Star"

    # Three White Soldiers
    if is_bullish(prev2) and is_bullish(prev1) and is_bullish(curr) and curr["close"] > prev1["close"] > prev2["close"]:
        return "BUY", "Three White Soldiers"

    # Three Black Crows
    if is_bearish(prev2) and is_bearish(prev1) and is_bearish(curr) and curr["close"] < prev1["close"] < prev2["close"]:
        return "SELL", "Three Black Crows"

    return "NEUTRAL", "No clear pattern"

def generate_signal(symbol, candles, sl_percent, reward_ratio):
    signal_type, pattern_name = detect_candlestick_pattern(candles)
    close_price = float(candles[-1]["close"])

    if signal_type == "NEUTRAL":
        return None

    if signal_type == "BUY":
        sl = close_price * (1 - sl_percent / 100)
        tp = close_price + (close_price - sl) * reward_ratio
        signal = "LONG"
    else:
        sl = close_price * (1 + sl_percent / 100)
        tp = close_price - (sl - close_price) * reward_ratio
        signal = "SHORT"

    return {
        "symbol": symbol,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signal": signal,
        "entry": format_price(close_price),
        "sl": format_price(sl),
        "tp": format_price(tp),
        "pattern": pattern_name,
        "indicators": candles[-1],
    }

# ===========================
# 4️⃣ Send Signal to Telegram
# ===========================
def send_signal_to_telegram(sig):
    indicators_text = "\n".join([
        f"- {k}: {format_price(sig['indicators'].get(k, 0))}" for k in KEY_INDICATORS
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
    safe_get(TELEGRAM_API_URL, {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})

# ===========================
# 5️⃣ Daily Summary
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
        f"📊 Total: {total} signals today"
    )
    safe_get(TELEGRAM_API_URL, {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})

def log_signal(sig, filename="signals_log.jsonl"):
    with open(filename, "a") as f:
        f.write(json.dumps(sig) + "\n")

# ===========================
# 6️⃣ Main Loop
# ===========================
def main():
    print("🚀 Full Candlestick Signal Bot Started...")
    last_signals = load_json_file(LAST_SIGNAL_FILE, {})

    while True:
        symbols_config = read_symbols_config()

        for symbol, config in symbols_config.items():
            try:
                # Fetch last 3 candles (simulate historical data)
                handler = TA_Handler(
                    symbol=symbol,
                    screener="crypto",
                    exchange="BINANCE",
                    interval=Interval.INTERVAL_1_HOUR
                )
                # Ideally, fetch historical candles here
                analysis = handler.get_analysis()
                # Simulate last 3 candles
                candles = [analysis.indicators, analysis.indicators, analysis.indicators]  # replace with real historical data

                signal = generate_signal(symbol, candles, config["sl_percent"], config["reward_ratio"])

                if signal and signal != last_signals.get(symbol):
                    send_signal_to_telegram(signal)
                    log_signal(signal)
                    update_daily_stats(signal["signal"])
                    last_signals[symbol] = signal
                    save_json_file(LAST_SIGNAL_FILE, last_signals)
                    print(f"✅ New signal for {symbol}: {signal['signal']} ({signal['pattern']})")
                else:
                    print(f"ℹ️ No new signal for {symbol}")

            except Exception as e:
                print(f"⚠️ Error for {symbol}: {e}")

        # Daily summary at midnight
        now = datetime.now()
        if now.hour == 0 and now.minute == 0:
            send_daily_summary()
            print("📤 Daily summary sent!")

        # Sleep until next hour
        next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        sleep_seconds = (next_hour - now).total_seconds()
        print(f"⏳ Sleeping {int(sleep_seconds)}s until next hour...")
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    main()
