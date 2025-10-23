import os
import time
import random
import logging
import requests
from datetime import datetime, timedelta, timezone
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv
from pathlib import Path
from requests.exceptions import RequestException

# ============================
# 1️⃣ Setup
# ============================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]  # Add your symbols here
INTERVAL = Interval.INTERVAL_15_MINUTES
VOLATILITY = 0.005  # 0.5% default for TP/SL

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Tracking
last_signal = {}
open_positions = {}  # symbol: {entry, side, time, sl, tp1, tp2, tp1_sent, tp2_sent, sl_sent}
last_request_time = {symbol: 0 for symbol in SYMBOLS}
MIN_INTERVAL = 60  # seconds between requests per symbol
SUMMARY_INTERVAL = 300  # 5 minutes
last_summary_time = 0
multi_tf_cache = {}  # symbol: {interval: analysis}

# ============================
# 2️⃣ Telegram helpers
# ============================
def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram not configured. Skipping message.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10).raise_for_status()
    except RequestException as e:
        logging.error(f"Telegram error: {e}")

# ============================
# 3️⃣ TradingView fetch with exponential backoff
# ============================
def get_tv_analysis(symbol, interval=INTERVAL, retries=5):
    for attempt in range(1, retries + 1):
        try:
            # enforce per-symbol cooldown
            now = time.time()
            if now - last_request_time[symbol] < MIN_INTERVAL:
                wait = MIN_INTERVAL - (now - last_request_time[symbol])
                logging.warning(f"⏳ Waiting {wait:.1f}s for {symbol} due to rate limit")
                time.sleep(wait)
            last_request_time[symbol] = time.time()

            handler = TA_Handler(symbol=symbol, screener="crypto", exchange="BINANCE", interval=interval)
            analysis = handler.get_analysis()
            multi_tf_cache[symbol] = multi_tf_cache.get(symbol, {})
            multi_tf_cache[symbol][interval] = analysis  # cache analysis
            return analysis

        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                wait_time = 30 * (2 ** (attempt - 1))  # exponential backoff
                logging.warning(f"⚠️ TV 429 for {symbol} attempt {attempt}: wait {wait_time:.1f}s")
                time.sleep(wait_time)
            else:
                logging.warning(f"TV error {symbol} attempt {attempt}: {e}")
                time.sleep(5)
    logging.error(f"❌ Failed to fetch analysis for {symbol} after {retries} attempts.")
    return None

# ============================
# 4️⃣ Compute TP/SL levels
# ============================
def compute_levels(price, signal):
    if signal in ["BUY", "STRONG_BUY"]:
        sl = round(price * (1 - VOLATILITY), 4)
        tp1 = round(price * (1 + VOLATILITY * 2), 4)
        tp2 = round(price * (1 + VOLATILITY * 4), 4)
    elif signal in ["SELL", "STRONG_SELL"]:
        sl = round(price * (1 + VOLATILITY), 4)
        tp1 = round(price * (1 - VOLATILITY * 2), 4)
        tp2 = round(price * (1 - VOLATILITY * 4), 4)
    else:
        sl = tp1 = tp2 = price
    return sl, tp1, tp2

# ============================
# 5️⃣ Format message
# ============================
def format_message(symbol, signal, open_price, close_price, rsi, macd, macd_signal, tf):
    timestamp = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"⏰ {timestamp}\n"
        f"📌 Symbol: {symbol}\n"
        f"💹 Signal: <b>{signal}</b>\n"
        f"💰 Entry: {close_price}\n"
        f"🕓 Timeframe: {tf}\n\n"
        f"📊 Key Indicators:\n"
        f"- Open: {open_price}\n"
        f"- Close: {close_price}\n"
        f"- RSI: {rsi:.2f}\n"
        f"- MACD: {macd:.6f}\n"
        f"- Signal: {macd_signal:.6f}\n"
    )

# ============================
# 6️⃣ Check TP/SL & 10-min profit
# ============================
def check_trade(symbol, price):
    if symbol not in open_positions:
        return

    trade = open_positions[symbol]
    entry, side, sl, tp1, tp2, start_time = trade["entry"], trade["side"], trade["sl"], trade["tp1"], trade["tp2"], trade["time"]

    # TP1
    if not trade.get("tp1_sent", False):
        if (side == "BUY" and price >= tp1) or (side == "SELL" and price <= tp1):
            send_telegram(f"⚡ {symbol} TP1 hit! Current: {price}, Side: {side}")
            trade["tp1_sent"] = True

    # TP2
    if not trade.get("tp2_sent", False):
        if (side == "BUY" and price >= tp2) or (side == "SELL" and price <= tp2):
            send_telegram(f"🏆 {symbol} TP2 hit! Current: {price}, Side: {side}")
            trade["tp2_sent"] = True
            del open_positions[symbol]

    # SL
    if not trade.get("sl_sent", False):
        if (side == "BUY" and price <= sl) or (side == "SELL" and price >= sl):
            send_telegram(f"🛑 {symbol} SL hit! Current: {price}, Side: {side}")
            trade["sl_sent"] = True
            del open_positions[symbol]

    # 10-min profit
    if datetime.now(timezone.utc) - start_time >= timedelta(minutes=10):
        profit = ((price - entry) / entry) * 100
        if side == "SELL":
            profit *= -1
        result = "✅ Profit" if profit > 0 else "❌ Loss"
        send_telegram(f"📊 {symbol} 10-min result: {result} ({profit:.2f}%)")
        if symbol in open_positions:
            del open_positions[symbol]

# ============================
# 7️⃣ Open trades summary
# ============================
def send_summary():
    if not open_positions:
        return
    timestamp = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S")
    msg = f"📊 <b>Open Trades Summary</b> ({timestamp} WIB)\n\n"
    for symbol, trade in open_positions.items():
        elapsed = datetime.now(timezone.utc) - trade["time"]
        msg += f"- {symbol}: {trade['side']} | Entry: {trade['entry']} | Open for {int(elapsed.total_seconds()/60)}m\n"
    send_telegram(msg)
    logging.info("Open trades summary sent ✅")

# ============================
# 8️⃣ Main loop
# ============================
while True:
    for symbol in SYMBOLS:
        analysis = get_tv_analysis(symbol)
        if not analysis:
            continue

        try:
            summary = analysis.summary
            indicators = analysis.indicators
            current_signal = summary.get("RECOMMENDATION", "HOLD")
            open_price = indicators.get("open", 0)
            close_price = indicators.get("close", 0)
            rsi = indicators.get("RSI", 0)
            macd = indicators.get("MACD.macd", 0)
            macd_signal = indicators.get("MACD.signal", 0)
            tf = analysis.time

            # Signal change alert
            if symbol not in last_signal or last_signal[symbol] != current_signal:
                last_signal[symbol] = current_signal
                msg = format_message(symbol, current_signal, open_price, close_price, rsi, macd, macd_signal, tf)
                send_telegram(msg)
                logging.info(f"{symbol} changed to {current_signal} → Telegram alert sent ✅")

                # Record entry and compute TP/SL
                if current_signal in ["BUY", "STRONG_BUY", "SELL", "STRONG_SELL"]:
                    sl, tp1, tp2 = compute_levels(close_price, current_signal)
                    open_positions[symbol] = {
                        "entry": close_price,
                        "side": "BUY" if "BUY" in current_signal else "SELL",
                        "time": datetime.now(timezone.utc),
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp1_sent": False,
                        "tp2_sent": False,
                        "sl_sent": False
                    }

        except Exception as e:
            logging.error(f"❌ Error analyzing {symbol}: {e}")

        # Random sleep to avoid 429
        time.sleep(random.uniform(15, 60))

    # Open trades summary
    if time.time() - last_summary_time >= SUMMARY_INTERVAL:
        send_summary()
        last_summary_time = time.time()

    logging.info("⏳ Waiting before next round...")
    time.sleep(random.uniform(30, 60))
