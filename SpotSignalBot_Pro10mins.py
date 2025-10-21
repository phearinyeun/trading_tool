# FuturesSignalBot_10min.py
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import time
import os
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = ["ETH"]
VS_CURRENCY = "USD"
LIMIT = 50  # 10-min candles
SLEEP_SECONDS = 30  # check every 30 sec

# Futures leverage per coin
LEVERAGE = {"BTC":10, "ETH":20, "DOGE":5}
RISK_PERCENT = 0.02  # 2% of portfolio per trade
PORTFOLIO_USD = 26  # example portfolio
POSITION_PERCENT = 1/3 # split across 3 TPs

# Fees
FUTURES_FEE_RATE = 0.0004  # 0.04% per trade

# ---------------- TELEGRAM ----------------
def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured; skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# ---------------- TA HELPERS ----------------
def ema(series, span): return series.ewm(span=span, adjust=False).mean()
def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1*delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-12)
    return 100 - (100/(1+rs))
def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist
def atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

# ---------------- FETCH OHLC ----------------
def get_ohlc(symbol, limit=50):
    url = f"https://min-api.cryptocompare.com/data/v2/histominute?fsym={symbol}&tsym={VS_CURRENCY}&limit={limit}&aggregate=10"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("Response") != "Success":
        raise Exception(data.get("Message","Unknown error"))
    df = pd.DataFrame(data["Data"]["Data"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time","open","high","low","close"]]

# ---------------- CANDLE PATTERNS ----------------
def detect_pattern_from_df(df):
    if len(df) < 2: return None
    prev, last = df.iloc[-2], df.iloc[-1]
    o1, c1 = prev["open"], prev["close"]
    o2, c2 = last["open"], last["close"]
    body = abs(c2-o2)
    lower_shadow = min(o2,c2) - last["low"]
    upper_shadow = last["high"] - max(o2,c2)
    if (c2 > o2) and (c1 < o1) and (c2 > o1) and (o2 < c1): return "🟢 Bullish Engulfing"
    if (c2 < o2) and (c1 > o1) and (o2 > c1) and (c2 < o1): return "🔴 Bearish Engulfing"
    if lower_shadow > 2*body and upper_shadow < body: return "🟢 Hammer"
    if upper_shadow > 2*body and lower_shadow < body: return "🔴 Shooting Star"
    if body / (last["high"]-last["low"] + 1e-12) < 0.1: return "⚪ Doji"
    return None

# ---------------- SIGNAL ----------------
def find_signal_candle(df):
    close = df["close"]
    df["EMA50"], df["EMA200"] = ema(close,50), ema(close,200)
    df["RSI14"] = rsi(close,14)
    macd_line, macd_signal, macd_hist = macd(close)
    df["MACD_HIST"] = macd_hist
    last = df.iloc[-1]
    pattern = detect_pattern_from_df(df)
    confidence = 0
    if last["EMA50"] > last["EMA200"]: confidence += 25
    if last["MACD_HIST"] > 0: confidence += 25
    if last["RSI14"] < 70: confidence += 20
    if pattern and pattern.startswith("🟢"): confidence += 20
    confidence = min(confidence, 100)

    if last["EMA50"] > last["EMA200"] and last["MACD_HIST"] > 0 and last["RSI14"] < 70:
        return last, "STRONG_BUY", pattern, confidence
    elif last["EMA50"] < last["EMA200"] and last["MACD_HIST"] < 0 and last["RSI14"] > 30:
        return last, "STRONG_SELL", pattern, confidence
    elif pattern and pattern.startswith("🟢"):
        return last, "WEAK_BUY", pattern, confidence
    elif pattern and pattern.startswith("🔴"):
        return last, "WEAK_SELL", pattern, confidence
    else:
        return last, "HOLD", None, confidence

# ---------------- TRADE PLAN ----------------
def generate_futures_plan(df, symbol):
    candle, signal, pattern, confidence = find_signal_candle(df)
    if signal == "HOLD": return None

    atr_val = atr(df).iloc[-1]
    entry_price = float(candle["close"])
    risk_amount = PORTFOLIO_USD * RISK_PERCENT
    leverage = LEVERAGE.get(symbol, 10)

    if signal in ["STRONG_BUY","WEAK_BUY"]:
        sl = entry_price - 0.5*atr_val
        tp1 = entry_price + 0.5*atr_val
        tp2 = entry_price + 1*atr_val
        tp3 = entry_price + 1.5*atr_val
        direction = "LONG ✅"
        strength = "🚀 STRONG BUY" if signal=="STRONG_BUY" else "⚡ Weak BUY"
    else:
        sl = entry_price + 0.5*atr_val
        tp1 = entry_price - 0.5*atr_val
        tp2 = entry_price - 1*atr_val
        tp3 = entry_price - 1.5*atr_val
        direction = "SHORT ⛔"
        strength = "🔻 STRONG SELL" if signal=="STRONG_SELL" else "⚡ Weak SELL"

    pos1 = pos2 = pos3 = (risk_amount / abs(entry_price - sl)) * leverage * POSITION_PERCENT
    entry_time = (candle["time"] + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "signal": signal,
        "strength": strength,
        "confidence": confidence,
        "direction": direction,
        "pattern": pattern,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "pos1": pos1,
        "pos2": pos2,
        "pos3": pos3,
        "ohlc": {"open":float(candle["open"]), "high":float(candle["high"]),
                 "low":float(candle["low"]), "close":float(candle["close"])}
    }

# ---------------- NET PROFIT ----------------
def calc_futures_net_profit(entry, tp, size, fee_rate=FUTURES_FEE_RATE):
    gross_profit = (tp - entry) * size
    total_fee = (entry + tp) * size * fee_rate
    return gross_profit - total_fee

# ---------------- COUNTDOWN ----------------
def get_countdown(next_candle):
    jakarta_tz = timezone(timedelta(hours=7))
    now_jakarta = datetime.now(jakarta_tz)
    delta = next_candle - now_jakarta
    total_seconds = max(int(delta.total_seconds()),0)
    minutes, seconds = divmod(total_seconds,60)
    return f"{minutes}m {seconds}s"

# ---------------- FORMAT MESSAGE ----------------
def format_trade_message(symbol, plan):
    jakarta_tz = timezone(timedelta(hours=7))
    next_candle_utc = datetime.strptime(plan['entry_time'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    next_candle_jakarta = next_candle_utc.astimezone(jakarta_tz)
    countdown = get_countdown(next_candle_jakarta)

    msg = f"{plan['strength']} *{symbol} ({VS_CURRENCY}) Analysis*\n"
    msg += f"🕐 Next Candle Entry: {next_candle_jakarta.strftime('%Y-%m-%d %H:%M:%S')} (UTC+07:00)\n"
    msg += f"⏳ Time until next candle: {countdown}\n"
    msg += f"💰 Open: {plan['ohlc']['open']:.4f}\n"
    msg += f"📈 High: {plan['ohlc']['high']:.4f}\n"
    msg += f"📉 Low: {plan['ohlc']['low']:.4f}\n"
    msg += f"🔚 Close: {plan['ohlc']['close']:.4f}\n\n"
    msg += f"📘 Pattern: {plan['pattern'] or 'None'}\n"
    msg += f"📊 Signal: {plan['strength']} ({plan['confidence']}% confidence)\n"
    msg += f"📍 Direction: {plan['direction']}\n\n"
    msg += f"💰 Entry: {plan['entry_price']:.4f}\n"
    msg += f"🛑 Stop Loss: {plan['sl']:.4f}\n"
    msg += f"🎯 Take Profit 1: {plan['tp1']:.4f} ({plan['pos1']:.4f} units, Net Profit: ${calc_futures_net_profit(plan['entry_price'], plan['tp1'], plan['pos1']):.2f})\n"
    msg += f"🎯 Take Profit 2: {plan['tp2']:.4f} ({plan['pos2']:.4f} units, Net Profit: ${calc_futures_net_profit(plan['entry_price'], plan['tp2'], plan['pos2']):.2f})\n"
    msg += f"🎯 Take Profit 3: {plan['tp3']:.4f} ({plan['pos3']:.4f} units, Net Profit: ${calc_futures_net_profit(plan['entry_price'], plan['tp3'], plan['pos3']):.2f})\n"
    return msg

# ---------------- MAIN LOOP ----------------
def main():
    while True:
        for symbol in SYMBOLS:
            try:
                df = get_ohlc(symbol, LIMIT)
                plan = generate_futures_plan(df, symbol)
                if plan:
                    msg = format_trade_message(symbol, plan)
                    send_telegram_message(msg)
                    print(f"Sent signal for {symbol}")
            except Exception as e:
                print(f"❌ Error fetching data for {symbol}: {e}")
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()
