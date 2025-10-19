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
# 3️⃣ Fetch USDT Trading Pairs from CoinGecko
# ===========================
def get_usdt_trading_pairs():
    url = "https://api.coingecko.com/api/v3/exchanges/binance/tickers"
    response = safe_get(url)
    if not response:
        print("⚠️ Failed to fetch USDT trading pairs from CoinGecko.")
        return ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]  # fallback default
    data = response.json()
    usdt_pairs = [t['base'] + 'USDT' for t in data['tickers'] if t['target'] == 'USDT']
    return list(set(usdt_pairs))  # remove duplicates

def create_symbols_config(usdt_pairs):
    config = {}
    for symbol in usdt_pairs:
        config[symbol] = {"sl_percent": 0.5, "reward_ratio": 2}
    return config

# ===========================
# 4️⃣ TradingView Safe Analysis
# ===========================
def get_analysis_safe(handler, retries=5):
    wait_time = 5
    for attempt in range(retries):
        try:
            return handler.get_analysis()
        except Exception as e:
            if "429" in str(e):
                print(f"⚠️ Rate limit hit. Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                wait_time *= 2  # exponential backoff
            else:
                print(f"⚠️ TradingView API error: {e}")
                return None
    return None

# ===========================
# 5️⃣ Signal Generation
# ===========================
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

# ===========================
# 6️⃣ Telegram & Logging
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

def update_daily_stats(signal_type):
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
# 7️⃣ Main Loop: 10-Minute Signals
# ===========================
def main():
    print("🚀 TradingView 10-Minute Signal Bot Started...")
    last_signals = load_json_file(LAST_SIGNAL_FILE, {})
    if not isinstance(last_signals, dict):
        last_signals = {}

    last_summary_time = datetime.now()

    # Fetch USDT pairs from CoinGecko
    usdt_pairs = get_usdt_trading_pairs()
    symbols_config = create_symbols_config(usdt_pairs)

    while True:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for symbol, config in symbols_config.items():
            try:
                handler = TA_Handler(
                    symbol=symbol,
                    screener="crypto",
                    exchange="BINANCE",
                    interval=Interval.INTERVAL_5_MINUTES
                )

                analysis = get_analysis_safe(handler)
                if not analysis:
                    continue

                signals = generate_signal_tv_json(
                    symbol, analysis, current_time,
                    sl_percent=config.get("sl_percent", 0.5),
                    reward_ratio=config.get("reward_ratio", 2)
                )

                last_signal_entry = last_signals.get(symbol, {})
                last_signal_time = last_signal_entry.get("time") if isinstance(last_signal_entry, dict) else None

                # Only send if 10 minutes passed
                if signals and (
                    not last_signal_time or
                    datetime.strptime(last_signal_time, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=10) <= datetime.now()
                ):
                    send_signal_to_telegram(signals)
                    log_signals_to_file(signals)
                    for s in signals:
                        update_daily_stats(s["signal"])

                    last_signals[symbol] = {"signals": signals, "time": current_time}
                    save_json_file(LAST_SIGNAL_FILE, last_signals)
                    print(f"✅ Sent new signal for {symbol} at {current_time}")
                else:
                    print(f"ℹ️ No new signal for {symbol} at {current_time}")

                time.sleep(5)  # small delay between symbols

            except Exception as e:
                print(f"⚠️ Error for {symbol}: {e}")

        # Daily summary
        if datetime.now() - last_summary_time >= timedelta(hours=24):
            send_daily_summary()
            last_summary_time = datetime.now()
            print("📤 Daily summary sent!")

        print("⏳ Waiting 10 minutes before next scan...\n")
        time.sleep(600)

if __name__ == "__main__":
    main()
