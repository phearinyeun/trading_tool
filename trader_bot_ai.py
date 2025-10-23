import os
import time
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import deque

import requests
import pandas as pd
import matplotlib.pyplot as plt
import ta
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv

from xgboost import XGBClassifier
import pickle
import numpy as np

# ===============================
# 1️⃣ Load environment
# ===============================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "").split(",") if s.strip()]
TV_SYMBOLS = [s.strip() for s in os.getenv("TV_SYMBOLS", "").split(",") if s.strip()]
CURRENCY = os.getenv("CURRENCY", "usd")
SLEEP_TIME = int(os.getenv("SLEEP_TIME", 60))
CANDLE_HISTORY = int(os.getenv("CANDLE_HISTORY", 50))
HOLD_ALERT_INTERVAL = int(os.getenv("HOLD_ALERT_INTERVAL", 1800))

if not SYMBOLS or not TV_SYMBOLS or len(SYMBOLS) != len(TV_SYMBOLS):
    raise ValueError("❌ SYMBOLS and TV_SYMBOLS must be set in .env and equal length")

JAKARTA_OFFSET = timedelta(hours=7)
CHARTS_DIR = Path("charts")
CHARTS_DIR.mkdir(exist_ok=True)
LOG_FILE = Path("alerts_log.csv")
if not LOG_FILE.exists():
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "symbol", "coin", "price", "decision", "reason", "expected_profit", "ai_confidence"])

# Tracking
last_signals = {tv: None for tv in TV_SYMBOLS}
hold_start_time = {tv: None for tv in TV_SYMBOLS}
price_history = {tv: deque(maxlen=CANDLE_HISTORY) for tv in TV_SYMBOLS}


# ===============================
# 2️⃣ Telegram
# ===============================
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass


def send_telegram_image(image_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as photo:
        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        files = {"photo": photo}
        try:
            requests.post(url, data=data, files=files, timeout=10)
        except:
            pass


# ===============================
# 3️⃣ Fetch CoinGecko price
# ===============================
def get_price_data(symbol):
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": symbol, "vs_currencies": CURRENCY, "include_24hr_change": "true"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()[symbol]
    except:
        return None


# ===============================
# 4️⃣ Fetch TradingView TA
# ===============================
def get_tv_signal(tv_symbol, interval=Interval.INTERVAL_1_HOUR):
    try:
        handler = TA_Handler(symbol=tv_symbol, screener="crypto", exchange="BINANCE", interval=interval)
        return handler.get_analysis().summary.get("RECOMMENDATION", "HOLD")
    except:
        return "HOLD"


# ===============================
# 5️⃣ Candlestick pattern detection
# ===============================
def detect_pattern(prices):
    if len(prices) < 3: return "NONE"
    o = [p[0] for p in prices]
    if abs(o[-1] - o[-2]) < 0.001 * o[-1]: return "Doji"
    if o[-1] > o[-2] and o[-2] < o[-3]: return "Bullish Engulfing"
    if o[-1] < o[-2] and o[-2] > o[-3]: return "Bearish Engulfing"
    return "NONE"


# ===============================
# 6️⃣ TP/SL computation
# ===============================
def compute_levels(price, signal, vol=0.005):
    entry = price
    if signal == "BUY":
        sl = round(price * (1 - vol), 4);
        tp1 = round(price * (1 + vol * 2), 4);
        tp2 = round(price * (1 + vol * 4), 4)
    elif signal == "SELL":
        sl = round(price * (1 + vol), 4);
        tp1 = round(price * (1 - vol * 2), 4);
        tp2 = round(price * (1 - vol * 4), 4)
    else:
        sl = tp1 = tp2 = price
    return entry, sl, tp1, tp2


# ===============================
# 7️⃣ Feature extraction for AI
# ===============================
def extract_features(prices):
    df = pd.DataFrame(prices, columns=["price"])
    df["MA20"] = df["price"].rolling(20).mean().fillna(df["price"])
    df["MA50"] = df["price"].rolling(50).mean().fillna(df["price"])
    df["MA200"] = df["price"].rolling(200).mean().fillna(df["price"])
    df["RSI"] = ta.momentum.RSIIndicator(df["price"]).rsi().fillna(50)
    df["MACD"] = ta.trend.MACD(df["price"]).macd_diff().fillna(0)
    # Previous N price differences
    for i in range(1, 4):
        df[f"diff{i}"] = df["price"].diff(i).fillna(0)
    return df.iloc[-1].values.reshape(1, -1)


# ===============================
# 8️⃣ Generate chart
# ===============================
def generate_chart(tv_symbol, prices, entry, sl, tp1, tp2, signals=None, ai_conf=None):
    df = pd.DataFrame(prices, columns=["price"])
    plt.figure(figsize=(12, 5))
    plt.plot(df["price"], label="Price", color="blue")
    df["MA20"] = df["price"].rolling(20).mean()
    df["MA50"] = df["price"].rolling(50).mean()
    df["MA200"] = df["price"].rolling(200).mean()
    plt.plot(df["MA20"], label="MA20", color="green")
    plt.plot(df["MA50"], label="MA50", color="orange")
    plt.plot(df["MA200"], label="MA200", color="purple")
    plt.axhline(entry, color='green', linestyle='--', label='Entry')
    plt.axhline(sl, color='red', linestyle='--', label='SL')
    plt.axhline(tp1, color='orange', linestyle='--', label='TP1')
    plt.axhline(tp2, color='purple', linestyle='--', label='TP2')
    if signals:
        for idx, signal in signals:
            price = df["price"].iloc[idx]
            if signal == "BUY":
                plt.annotate('BUY', xy=(idx, price), xytext=(idx, price * 0.995),
                             arrowprops=dict(facecolor='green', shrink=0.05))
            elif signal == "SELL":
                plt.annotate('SELL', xy=(idx, price), xytext=(idx, price * 1.005),
                             arrowprops=dict(facecolor='red', shrink=0.05))
    if ai_conf is not None:
        plt.title(f"{tv_symbol} Price Chart | AI Confidence: {ai_conf * 100:.1f}%")
    else:
        plt.title(f"{tv_symbol} Price Chart")
    plt.xlabel("Candles");
    plt.ylabel("Price");
    plt.legend()
    ts = datetime.now(timezone.utc) + timedelta(hours=7)
    filename = CHARTS_DIR / f"{tv_symbol}_{ts.strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(filename);
    plt.close()
    return filename


# ===============================
# 9️⃣ Analyze symbol
# ===============================
# Load pre-trained AI model (XGBoost)
MODEL_FILE = "ai_model.pkl"
if not os.path.exists(MODEL_FILE):
    # For first-time, you need to train model separately
    print("❌ AI model not found. Train and save XGBoost model as ai_model.pkl")
    exit()
with open(MODEL_FILE, "rb") as f: ai_model = pickle.load(f)


def analyze_symbol(coin_symbol, tv_symbol):
    global last_signals, price_history, hold_start_time
    price_data = get_price_data(coin_symbol)
    if not price_data: return
    price = price_data[CURRENCY]
    price_history[tv_symbol].append([price])

    # Multi-timeframe TA signals
    signal_1h = get_tv_signal(tv_symbol, Interval.INTERVAL_1_HOUR)
    signal_4h = get_tv_signal(tv_symbol, Interval.INTERVAL_4_HOURS)
    signal_1d = get_tv_signal(tv_symbol, Interval.INTERVAL_1_DAY)
    ta_signals = [signal_1h, signal_4h, signal_1d]
    ta_decision = "HOLD"
    if ta_signals.count("BUY") >= 2:
        ta_decision = "BUY"
    elif ta_signals.count("SELL") >= 2:
        ta_decision = "SELL"

    # Candlestick
    pattern = detect_pattern(price_history[tv_symbol])

    # AI prediction
    features = extract_features(price_history[tv_symbol])
    ai_pred = ai_model.predict(features)[0]
    ai_conf = max(ai_model.predict_proba(features)[0])
    ai_signal = ["HOLD", "BUY", "SELL"][ai_pred]

    # Combine TA + AI
    decision = "HOLD"
    if ai_signal == ta_decision and ai_signal != "HOLD" and ai_conf > 0.55:
        decision = ai_signal

    # TP/SL
    entry, sl, tp1, tp2 = compute_levels(price, decision)
    expected_profit = ((tp1 - entry) / entry * 100) if decision == "BUY" else (
                (entry - tp1) / entry * 100) if decision == "SELL" else 0

    # HOLD alert
    jakarta_time = datetime.now(timezone.utc) + timedelta(hours=7)
    if decision == "HOLD":
        if hold_start_time[tv_symbol] is None:
            hold_start_time[tv_symbol] = jakarta_time
        else:
            elapsed = jakarta_time - hold_start_time[tv_symbol]
            if elapsed.total_seconds() >= HOLD_ALERT_INTERVAL:
                send_telegram(f"⚠️ {tv_symbol} has been on HOLD for {int(elapsed.total_seconds() / 60)} mins.")
                hold_start_time[tv_symbol] = jakarta_time
    else:
        hold_start_time[tv_symbol] = None

    # Alert if changed
    if last_signals[tv_symbol] != decision:
        msg = f"""
🚀 <b>Market Alert</b> 🚀
⏰ <b>{jakarta_time.strftime('%Y-%m-%d %H:%M:%S')} WIB</b>
💹 <b>{tv_symbol} ({coin_symbol})</b>
💰 Price: {price} {CURRENCY.upper()}
📈 Decision: {decision}
📝 Reason: Multi-timeframe TA + pattern {pattern} + AI signal {ai_signal}
⚡ TP/SL: Entry {entry}, SL {sl}, TP1 {tp1}, TP2 {tp2}
💵 Expected Profit: {expected_profit:.2f}%
🤖 AI Confidence: {ai_conf * 100:.1f}%
"""
        chart_file = generate_chart(tv_symbol, price_history[tv_symbol], entry, sl, tp1, tp2,
                                    signals=[
                                        (len(price_history[tv_symbol]) - 1, decision)] if decision != "HOLD" else None,
                                    ai_conf=ai_conf)
        send_telegram(msg)
        send_telegram_image(chart_file, caption=f"{tv_symbol} chart")
        last_signals[tv_symbol] = decision

        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([jakarta_time.strftime('%Y-%m-%d %H:%M:%S'), tv_symbol, coin_symbol, price, decision,
                             f"TA + {pattern} + AI {ai_signal}", expected_profit, ai_conf])


# ===============================
# 10️⃣ Main Loop
# ===============================
def main():
    print("🚀 Pro Trader Bot v4 with AI Started...")
    while True:
        for coin, tv in zip(SYMBOLS, TV_SYMBOLS):
            try:
                analyze_symbol(coin, tv)
            except Exception as e:
                print(f"❌ Error analyzing {tv}: {e}")
        time.sleep(SLEEP_TIME)


if __name__ == "__main__":
    main()
