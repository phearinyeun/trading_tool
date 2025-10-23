import os
import time
import csv
import requests
from datetime import datetime, timezone, timedelta
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv
from pathlib import Path
from requests.exceptions import RequestException, ConnectionError, Timeout
import matplotlib.pyplot as plt
from collections import deque

# ===============================
# 1️⃣ Load .env reliably
# ===============================
env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    raise FileNotFoundError(f"❌ .env file not found at {env_path}")

load_dotenv(dotenv_path=env_path)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Multiple symbols
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "").split(",") if s.strip()]
TV_SYMBOLS = [s.strip() for s in os.getenv("TV_SYMBOLS", "").split(",") if s.strip()]

if not SYMBOLS or not TV_SYMBOLS:
    raise ValueError("❌ SYMBOLS and TV_SYMBOLS must be set in .env")
if len(SYMBOLS) != len(TV_SYMBOLS):
    raise ValueError("❌ SYMBOLS and TV_SYMBOLS must have the same number of entries")

# Bot settings
CURRENCY = os.getenv("CURRENCY", "usd")
SLEEP_TIME = int(os.getenv("SLEEP_TIME", 60))
VOLATILITY = float(os.getenv("VOLATILITY", 0.005))
CANDLE_HISTORY = int(os.getenv("CANDLE_HISTORY", 30))
HOLD_ALERT_INTERVAL = int(os.getenv("HOLD_ALERT_INTERVAL", 1800))  # seconds

# Interval mapping
INTERVAL_STR = os.getenv("INTERVAL", "1h")
INTERVAL_MAPPING = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "30m": Interval.INTERVAL_30_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "2h": Interval.INTERVAL_2_HOURS,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
}
INTERVAL = INTERVAL_MAPPING.get(INTERVAL_STR.lower(), Interval.INTERVAL_1_HOUR)

# Track last signals, active targets, hold time
last_signals = {tv: None for tv in TV_SYMBOLS}
active_targets = {tv: {"tp1_sent": False, "tp2_sent": False, "sl_sent": False} for tv in TV_SYMBOLS}
hold_start_time = {tv: None for tv in TV_SYMBOLS}

# Keep last N prices for chart
price_history = {tv: deque(maxlen=CANDLE_HISTORY) for tv in TV_SYMBOLS}

# Jakarta timezone offset
JAKARTA_OFFSET = timedelta(hours=7)

# Folder to save charts
CHARTS_DIR = Path("charts")
CHARTS_DIR.mkdir(exist_ok=True)
MAX_CHARTS_PER_SYMBOL = 50  # keep last 50 charts per symbol

# CSV log file
LOG_FILE = Path("alerts_log.csv")
if not LOG_FILE.exists():
    with open(LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "coin", "price", "decision", "reason"])

# ===============================
# 2️⃣ Telegram Helpers
# ===============================
def send_telegram(message, retries=3):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("⚠️ Telegram not configured. Skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    for i in range(retries):
        try:
            requests.post(url, data=payload, timeout=10)
            return
        except Exception as e:
            print(f"❌ Telegram send error ({i+1}/{retries}):", e)
            time.sleep(2)

def send_telegram_image(image_path, caption="", retries=3):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("⚠️ Telegram not configured. Skipping image send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as photo:
        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        files = {"photo": photo}
        for i in range(retries):
            try:
                requests.post(url, data=data, files=files, timeout=10)
                return
            except Exception as e:
                print(f"❌ Telegram image send error ({i+1}/{retries}):", e)
                time.sleep(2)

# ===============================
# 3️⃣ CoinGecko Price Fetch
# ===============================
def get_price_data(symbol):
    if not symbol:
        return None
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": symbol, "vs_currencies": CURRENCY, "include_24hr_change": "true"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()[symbol]
    except (RequestException, KeyError) as e:
        print(f"❌ CoinGecko error for {symbol}: {e}")
        return None

# ===============================
# 4️⃣ TradingView TA with Retry & Fallback
# ===============================
def get_ta_signal(tv_symbol, retries=3, delay=5):
    for i in range(retries):
        try:
            handler = TA_Handler(symbol=tv_symbol, screener="crypto", exchange="BINANCE", interval=INTERVAL)
            analysis = handler.get_analysis()
            return analysis.summary
        except Exception as e:
            print(f"❌ TradingView error ({i+1}/{retries}) for {tv_symbol}: {e}")
            time.sleep(delay)
    prices = list(price_history[tv_symbol])
    if len(prices) < 2:
        return {"RECOMMENDATION":"HOLD"}
    return {"RECOMMENDATION":"BUY" if prices[-1] > prices[-2] else "SELL" if prices[-1] < prices[-2] else "HOLD"}

# ===============================
# 5️⃣ Compute Levels
# ===============================
def compute_levels(price, signal):
    entry = price
    if signal == "BUY":
        sl = round(price * (1 - VOLATILITY), 4)
        tp1 = round(price * (1 + VOLATILITY * 2), 4)
        tp2 = round(price * (1 + VOLATILITY * 4), 4)
    elif signal == "SELL":
        sl = round(price * (1 + VOLATILITY), 4)
        tp1 = round(price * (1 - VOLATILITY * 2), 4)
        tp2 = round(price * (1 - VOLATILITY * 4), 4)
    else:
        sl = tp1 = tp2 = price
    return entry, sl, tp1, tp2

# ===============================
# 6️⃣ Generate Price Chart
# ===============================
def generate_chart(tv_symbol, prices, entry, sl, tp1, tp2, signals=None):
    plt.figure(figsize=(10,5))
    plt.plot(list(prices), label='Price', color='blue')
    plt.axhline(entry, color='green', linestyle='--', label='Entry')
    plt.axhline(sl, color='red', linestyle='--', label='SL')
    plt.axhline(tp1, color='orange', linestyle='--', label='TP1')
    plt.axhline(tp2, color='purple', linestyle='--', label='TP2')
    if signals:
        for idx, signal in signals:
            price = list(prices)[idx]
            if signal == "BUY":
                plt.annotate('BUY', xy=(idx, price), xytext=(idx, price*0.995),
                             arrowprops=dict(facecolor='green', shrink=0.05), fontsize=10, color='green')
            elif signal == "SELL":
                plt.annotate('SELL', xy=(idx, price), xytext=(idx, price*1.005),
                             arrowprops=dict(facecolor='red', shrink=0.05), fontsize=10, color='red')
    plt.title(f"{tv_symbol} Price Chart")
    plt.xlabel("Candles")
    plt.ylabel("Price")
    plt.legend()
    timestamp = datetime.now(timezone.utc) + timedelta(hours=7)
    filename = CHARTS_DIR / f"{tv_symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(filename)
    plt.close()

    files = sorted(CHARTS_DIR.glob(f"{tv_symbol}_*.png"))
    while len(files) > MAX_CHARTS_PER_SYMBOL:
        files[0].unlink()
        files.pop(0)
    return filename

# ===============================
# 7️⃣ Analyze Symbol
# ===============================
def analyze_symbol(coin_symbol, tv_symbol):
    global last_signals, active_targets, price_history, hold_start_time

    price_data = get_price_data(coin_symbol)
    ta_data = get_ta_signal(tv_symbol)
    if not price_data or not ta_data:
        print(f"⚠️ Skipping {coin_symbol}/{tv_symbol} due to missing data.")
        return

    price = price_data[CURRENCY]
    change = price_data[f"{CURRENCY}_24h_change"]
    ta_signal = ta_data.get("RECOMMENDATION", "HOLD")
    price_history[tv_symbol].append(price)

    decision = "HOLD"
    reason = "Sideways market."
    confidence = "Low"
    if ta_signal == "BUY" and change > 1:
        decision = "BUY"
        reason = "Strong uptrend (TA + 24h growth)"
        confidence = "High"
    elif ta_signal == "SELL" and change < -1:
        decision = "SELL"
        reason = "Downtrend (TA + 24h drop)"
        confidence = "High"

    entry, sl, tp1, tp2 = compute_levels(price, decision)
    if decision == "BUY":
        expected_profit1 = ((tp1 - entry)/entry)*100
        expected_profit2 = ((tp2 - entry)/entry)*100
    elif decision == "SELL":
        expected_profit1 = ((entry - tp1)/entry)*100
        expected_profit2 = ((entry - tp2)/entry)*100
    else:
        expected_profit1 = expected_profit2 = 0

    jakarta_time = datetime.now(timezone.utc) + timedelta(hours=7)
    if last_signals[tv_symbol] != decision:
        active_targets[tv_symbol] = {"tp1_sent": False, "tp2_sent": False, "sl_sent": False}

    signals_to_plot = []
    if decision in ["BUY", "SELL"]:
        signals_to_plot.append((len(price_history[tv_symbol])-1, decision))

    # Send Telegram alert
    if last_signals[tv_symbol] != decision:
        msg = f"""
🚀 <b>Market Alert</b> 🚀
⏰ <b>{jakarta_time.strftime('%Y-%m-%d %H:%M:%S')} WIB</b>
💹 <b>Symbol:</b> {tv_symbol} ({coin_symbol})
💰 <b>Price:</b> {price} {CURRENCY.upper()}
📊 <b>24h Change:</b> {change:.2f}%
🧠 <b>TA Signal:</b> {ta_signal}
📈 <b>Decision:</b> {decision} ({confidence} confidence)
📝 <b>Reason:</b> {reason}
⚡ <b>Levels:</b>
    Entry: {entry}
    Stop Loss: {sl}
    TP1: {tp1} (~{expected_profit1:.2f}%)
    TP2: {tp2} (~{expected_profit2:.2f}%)
"""
        chart_file = generate_chart(tv_symbol, price_history[tv_symbol], entry, sl, tp1, tp2, signals=signals_to_plot)
        send_telegram_image(chart_file, caption=msg)

        # Log alert
        with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([jakarta_time.strftime('%Y-%m-%d %H:%M:%S'), tv_symbol, coin_symbol, price, decision, reason])

        last_signals[tv_symbol] = decision
        print(f"✅ Sent alert for {tv_symbol}: {decision}")

    # TP/SL alerts
    targets = active_targets[tv_symbol]
    if not targets["tp1_sent"] and ((decision=="BUY" and price>=tp1) or (decision=="SELL" and price<=tp1)):
        send_telegram(f"🎯 {tv_symbol} TP1 reached at {price} WIB")
        targets["tp1_sent"] = True
    if not targets["tp2_sent"] and ((decision=="BUY" and price>=tp2) or (decision=="SELL" and price<=tp2)):
        send_telegram(f"🏆 {tv_symbol} TP2 reached at {price} WIB")
        targets["tp2_sent"] = True
    if not targets["sl_sent"] and ((decision=="BUY" and price<=sl) or (decision=="SELL" and price>=sl)):
        send_telegram(f"⚠️ {tv_symbol} Stop Loss triggered at {price} WIB")
        targets["sl_sent"] = True

    # HOLD alert
    if decision == "HOLD":
        if hold_start_time[tv_symbol] is None:
            hold_start_time[tv_symbol] = jakarta_time
        else:
            elapsed = jakarta_time - hold_start_time[tv_symbol]
            if elapsed.total_seconds() >= HOLD_ALERT_INTERVAL:
                send_telegram(f"⚠️ {tv_symbol} has been on HOLD for {int(elapsed.total_seconds()/60)} minutes. Market indecisive.")
                hold_start_time[tv_symbol] = jakarta_time
    else:
        hold_start_time[tv_symbol] = None

# ===============================
# 8️⃣ Main Loop
# ===============================
def main():
    print("🚀 Multi-Symbol Market Analyzer with Logs & HOLD Alerts Started...")
    while True:
        try:
            for coin, tv in zip(SYMBOLS, TV_SYMBOLS):
                analyze_symbol(coin, tv)
        except (ConnectionError, Timeout) as e:
            print("🌐 Network error:", e)
        except Exception as e:
            print("❌ Unexpected error:", e)
        time.sleep(SLEEP_TIME)

if __name__ == "__main__":
    main()
