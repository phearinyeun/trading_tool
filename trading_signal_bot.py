import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone
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

def load_json_file(filename, default_data):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to load {filename}: {e}")
    return default_data

def save_json_file(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save {filename}: {e}")

def get_entry_timestamps():
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo("Asia/Phnom_Penh")
    except Exception:
        import pytz
        local_tz = pytz.timezone("Asia/Phnom_Penh")

    utc_now = datetime.now(timezone.utc)
    local_now = utc_now.astimezone(local_tz)
    fmt = "%Y-%m-%d %H:%M:%S"
    return {
        "utc": utc_now.strftime(fmt) + " UTC",
        "local": local_now.strftime(fmt) + " (Asia/Phnom_Penh)"
    }

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
# 3️⃣ Safe HTTP Request
# ===========================
def safe_get(url, params, retries=5, delay=5):
    for attempt in range(1, retries+1):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response
            else:
                print(f"❌ Attempt {attempt}/{retries} failed: {response.status_code} {response.text}")
        except RequestException as e:
            print(f"❌ Attempt {attempt}/{retries} exception: {e}")
        time.sleep(delay)
    print("⚠️ All retries failed for URL:", url)
    return None

# ===========================
# 4️⃣ Telegram Error Reporting
# ===========================
def send_error_to_telegram(symbol, error_message):
    message = (
        f"⚠️ *Error Alert*\n\n"
        f"📌 Symbol: {symbol}\n"
        f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"❌ Error: {error_message}"
    )
    safe_get(TELEGRAM_API_URL, {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })

# ===========================
# 5️⃣ Generate Signal JSON
# ===========================
def generate_signal_tv_json(symbol, analysis, current_time, sl_percent, reward_ratio):
    close_price = float(analysis.indicators.get("close", 0))
    signals = []

    recommendation = analysis.summary.get("RECOMMENDATION", "NEUTRAL")
    pattern_name = f"TradingView Signal: {recommendation}"

    entry_times = get_entry_timestamps()

    if recommendation == "BUY":
        sl = close_price * (1 - sl_percent / 100)
        tp = close_price + (close_price - sl) * reward_ratio
        signals.append({
            "symbol": symbol,
            "time": current_time,
            "entry_time_utc": entry_times["utc"],
            "entry_time_local": entry_times["local"],
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
            "entry_time_utc": entry_times["utc"],
            "entry_time_local": entry_times["local"],
            "signal": "SHORT",
            "entry": format_price(close_price),
            "sl": format_price(sl),
            "tp": format_price(tp),
            "pattern": pattern_name,
            "indicators": analysis.indicators,
        })
    return signals

# ===========================
# 6️⃣ Send Signal to Telegram
# ===========================
def send_signal_to_telegram(signals):
    for sig in signals:
        indicators_text = "\n".join([
            f"- {k}: {format_price(sig['indicators'][k]) if k in sig['indicators'] else 'N/A'}"
            for k in KEY_INDICATORS
        ])

        # Countdown to entry
        try:
            entry_time_dt = datetime.strptime(sig['entry_time_local'], "%Y-%m-%d %H:%M:%S (Asia/Phnom_Penh)")
        except:
            entry_time_dt = datetime.now()
        now_dt = datetime.now()
        countdown_min = max(int((entry_time_dt - now_dt).total_seconds() / 60), 0)

        message = (
            f"📢 *Futures Entry Signal*\n\n"
            f"⏰ *Generated At:* {sig['time']}\n"
            f"📅 *Entry Time (Local):* {sig['entry_time_local']}\n"
            f"🌍 *Entry Time (UTC):* {sig['entry_time_utc']}\n"
            f"⏳ *Entry in:* {countdown_min} minutes\n\n"
            f"📌 *Symbol:* {sig['symbol']}\n"
            f"💹 *Signal:* {sig['signal']}\n"
            f"💰 *Entry:* `{sig['entry']}`\n"
            f"🛑 *Stop Loss:* `{sig['sl']}`\n"
            f"🎯 *Take Profit:* `{sig['tp']}`\n"
            f"🔹 *Pattern:* {sig['pattern']}\n\n"
            f"📊 *Key Indicators:*\n{indicators_text}"
        )

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

# ===========================
# 7️⃣ Daily Stats
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

def log_signals_to_file(signals, filename="signals_log.jsonl"):
    with open(filename, "a") as f:
        for sig in signals:
            f.write(json.dumps(sig) + "\n")

# ===========================
# 8️⃣ Real-Time Bot Loop
# ===========================
def main():
    print("🚀 TradingView Real-Time Signal Bot Started...")
    last_signals = load_json_file(LAST_SIGNAL_FILE, {})
    last_summary_time = datetime.now()

    POLL_INTERVAL = 10  # seconds

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

                current_signal_key = signals[0]["signal"] if signals else None
                last_signal_key = last_signals.get(symbol, {}).get("signal")

                if current_signal_key and current_signal_key != last_signal_key:
                    send_signal_to_telegram(signals)
                    log_signals_to_file(signals)
                    for s in signals:
                        update_daily_stats(s["signal"])
                    last_signals[symbol] = signals[0]
                    save_json_file(LAST_SIGNAL_FILE, last_signals)
                    print(f"✅ Sent new signal for {symbol} at {current_time}")
                    print(f"🕒 Entry Time (Local): {signals[0]['entry_time_local']}")
                    print(f"🌍 Entry Time (UTC): {signals[0]['entry_time_utc']}")
                else:
                    print(f"ℹ️ No new signal for {symbol} at {current_time}")

            except Exception as e:
                error_msg = str(e)
                print(f"⚠️ Error for {symbol}: {error_msg}")
                send_error_to_telegram(symbol, error_msg)

        # Daily summary every 24 hours
        if datetime.now() - last_summary_time >= timedelta(hours=24):
            send_daily_summary()
            last_summary_time = datetime.now()
            print("📤 Daily summary sent!")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
