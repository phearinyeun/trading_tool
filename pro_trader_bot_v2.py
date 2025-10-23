# pro_trader_bot_with_live_profit.py
import os
import time
import csv
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import deque

import requests
import matplotlib.pyplot as plt
import pandas as pd
from tradingview_ta import TA_Handler, Interval
from dotenv import load_dotenv

# -------------------------
# Config / Env
# -------------------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "").split(",") if s.strip()]
TV_SYMBOLS = [s.strip() for s in os.getenv("TV_SYMBOLS", "").split(",") if s.strip()]
CURRENCY = os.getenv("CURRENCY", "usd").lower()
SLEEP_TIME = int(os.getenv("SLEEP_TIME", 60))
CANDLE_HISTORY = int(os.getenv("CANDLE_HISTORY", 50))
HOLD_ALERT_INTERVAL = int(os.getenv("HOLD_ALERT_INTERVAL", 1800))  # seconds
TRADE_EVAL_SECONDS = int(os.getenv("TRADE_EVAL_SECONDS", 600))  # 10 minutes default
DASHBOARD_INTERVAL = int(os.getenv("DASHBOARD_INTERVAL", 1800))  # send dashboard every 30 min

if not SYMBOLS or not TV_SYMBOLS or len(SYMBOLS) != len(TV_SYMBOLS):
    raise ValueError("❌ SYMBOLS and TV_SYMBOLS must be set in .env and equal length")

JAKARTA_OFFSET = timedelta(hours=7)
CHARTS_DIR = Path("charts")
CHARTS_DIR.mkdir(exist_ok=True)

# -------------------------
# Logging
# -------------------------
LOG_FILE = Path("alerts_log.csv")
TRADES_FILE = Path("trades_log.csv")
LOGGING_FILE = Path("bot_debug.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGGING_FILE),
        logging.StreamHandler()
    ],
)

if not LOG_FILE.exists():
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "tv_symbol", "coin_id", "price", "decision", "reason", "expected_profit", "tp_hit", "sl_hit"])

if not TRADES_FILE.exists():
    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "trade_start", "tv_symbol", "coin_id", "side", "entry_price", "sl", "tp1", "tp2", "exit_time", "exit_price",
            "exit_reason", "profit_pct", "duration_seconds"
        ])

# -------------------------
# State
# -------------------------
last_signals = {tv: None for tv in TV_SYMBOLS}
hold_start_time = {tv: None for tv in TV_SYMBOLS}
price_history = {tv: deque(maxlen=CANDLE_HISTORY) for tv in TV_SYMBOLS}
active_targets = {tv: {"tp1_sent": False, "tp2_sent": False, "sl_sent": False} for tv in TV_SYMBOLS}

# Simulated trades tracked per tv_symbol (only one active trade per symbol in this simplified model)
active_trades = {}  # tv_symbol -> trade dict

# Session
http = requests.Session()
http.headers.update({"User-Agent": "ProTraderBot/1.0"})

# -------------------------
# Telegram helpers
# -------------------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        logging.warning("Telegram not configured — skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        r = http.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            logging.error("Telegram send failed: %s %s", r.status_code, r.text)
    except Exception as e:
        logging.exception("Telegram send error: %s", e)


def send_telegram_image(image_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        logging.warning("Telegram not configured — skipping image send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as photo:
            data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            files = {"photo": photo}
            r = http.post(url, data=data, files=files, timeout=20)
            if r.status_code != 200:
                logging.error("Telegram image send failed: %s %s", r.status_code, r.text)
    except Exception as e:
        logging.exception("Telegram image send error: %s", e)

# -------------------------
# Price & TV helpers
# -------------------------
def get_price_data(symbol_id):
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": symbol_id, "vs_currencies": CURRENCY, "include_24hr_change": "true"}
    try:
        r = http.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get(symbol_id)
    except Exception as e:
        logging.exception("CoinGecko error for %s: %s", symbol_id, e)
        return None


def get_tv_signal(tv_symbol, interval=Interval.INTERVAL_1_HOUR, retries=3):
    attempt = 0
    backoff = 1.0
    while attempt < retries:
        try:
            handler = TA_Handler(symbol=tv_symbol, screener="crypto", exchange="BINANCE", interval=interval)
            analysis = handler.get_analysis()
            summary = analysis.summary if hasattr(analysis, "summary") else {}
            rec = summary.get("RECOMMENDATION") if isinstance(summary, dict) else None
            if rec:
                return rec
            return "HOLD"
        except Exception as e:
            attempt += 1
            logging.warning("TV error for %s attempt %d: %s", tv_symbol, attempt, e)
            time.sleep(backoff)
            backoff *= 2
    return "HOLD"

# -------------------------
# Patterns / Levels / Chart
# -------------------------
def detect_candlestick_pattern(closes):
    if len(closes) < 3:
        return "NONE"
    a, b, c = closes[-3], closes[-2], closes[-1]
    if abs(c - b) <= 0.0008 * c:
        return "Doji"
    if a < b < c:
        return "Bullish sequence"
    if a > b > c:
        return "Bearish sequence"
    return "NONE"


def _price_decimals(price):
    if price >= 100:
        return 2
    if price >= 1:
        return 4
    return 6


def compute_levels(price, signal, volatility=0.005):
    decimals = _price_decimals(price)
    entry = price
    if signal == "BUY":
        sl = round(price * (1 - volatility), decimals)
        tp1 = round(price * (1 + volatility * 2), decimals)
        tp2 = round(price * (1 + volatility * 4), decimals)
    elif signal == "SELL":
        sl = round(price * (1 + volatility), decimals)
        tp1 = round(price * (1 - volatility * 2), decimals)
        tp2 = round(price * (1 - volatility * 4), decimals)
    else:
        sl = tp1 = tp2 = round(price, decimals)
    return entry, sl, tp1, tp2


def generate_chart(tv_symbol, closes, entry, sl, tp1, tp2, signals=None):
    df = pd.DataFrame({"close": list(closes)})
    plt.figure(figsize=(12, 5))
    plt.plot(df["close"], label="Price")
    if len(df) >= 20:
        df["MA20"] = df["close"].rolling(20, min_periods=1).mean()
        plt.plot(df["MA20"], label="MA20")
    if len(df) >= 50:
        df["MA50"] = df["close"].rolling(50, min_periods=1).mean()
        plt.plot(df["MA50"], label="MA50")
    plt.axhline(entry, linestyle="--", label="Entry")
    plt.axhline(sl, linestyle="--", label="SL")
    plt.axhline(tp1, linestyle="--", label="TP1")
    plt.axhline(tp2, linestyle="--", label="TP2")
    if signals:
        for idx, sig in signals:
            if 0 <= idx < len(df):
                pv = df["close"].iloc[idx]
                plt.annotate(sig, xy=(idx, pv), xytext=(idx, pv * (0.995 if sig == "BUY" else 1.005)),
                             arrowprops=dict(arrowstyle="->"))
    plt.title(f"{tv_symbol} Price Chart with TP/SL")
    plt.xlabel("Candles")
    plt.ylabel("Price")
    plt.legend()
    timestamp = datetime.now(timezone.utc) + JAKARTA_OFFSET
    filename = CHARTS_DIR / f"{tv_symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(filename, bbox_inches="tight")
    plt.close()
    return filename

# -------------------------
# Trade lifecycle (simulated)
# -------------------------
def open_trade(tv_symbol, coin_id, side, entry_price, sl, tp1, tp2):
    start_time = datetime.now(timezone.utc)
    trade = {
        "start_time": start_time,
        "tv_symbol": tv_symbol,
        "coin_id": coin_id,
        "side": side,  # "BUY" or "SELL"
        "entry_price": entry_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "status": "OPEN"
    }
    active_trades[tv_symbol] = trade
    logging.info("Opened trade: %s %s @ %s", tv_symbol, side, entry_price)
    return trade


def close_trade(tv_symbol, exit_price, exit_reason):
    trade = active_trades.get(tv_symbol)
    if not trade:
        return
    end_time = datetime.now(timezone.utc)
    duration = (end_time - trade["start_time"]).total_seconds()
    side = trade["side"]
    entry = trade["entry_price"]
    profit_pct = 0.0
    if side == "BUY":
        profit_pct = (exit_price - entry) / entry * 100
    else:
        profit_pct = (entry - exit_price) / entry * 100
    # Write trade CSV
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            trade["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
            trade["tv_symbol"],
            trade["coin_id"],
            side,
            entry,
            trade["sl"],
            trade["tp1"],
            trade["tp2"],
            end_time.strftime("%Y-%m-%d %H:%M:%S"),
            exit_price,
            exit_reason,
            f"{profit_pct:.6f}",
            int(duration)
        ])
    logging.info("Closed trade %s reason=%s exit_price=%s profit_pct=%.4f duration=%ds",
                 tv_symbol, exit_reason, exit_price, profit_pct, int(duration))
    # send telegram summary short
    send_telegram(
        f"🏁 <b>Trade Closed</b>\n"
        f"{trade['tv_symbol']} ({trade['coin_id']})\n"
        f"Side: <b>{side}</b>\n"
        f"Entry: {entry}\n"
        f"Exit: {exit_price}\n"
        f"Reason: {exit_reason}\n"
        f"Profit: <b>{profit_pct:.4f}%</b>\n"
        f"Duration: {int(duration)}s"
    )
    # remove from active trades
    active_trades.pop(tv_symbol, None)

# -------------------------
# Analysis per symbol
# -------------------------
def analyze_symbol(coin_symbol, tv_symbol):
    global last_signals, price_history, hold_start_time, active_targets

    price_data = get_price_data(coin_symbol)
    if not price_data or CURRENCY not in price_data:
        logging.warning("No price for %s", coin_symbol)
        return
    price = float(price_data[CURRENCY])
    price_history[tv_symbol].append(price)

    # Fetch TV signals
    s1 = get_tv_signal(tv_symbol, Interval.INTERVAL_1_HOUR)
    s4 = get_tv_signal(tv_symbol, Interval.INTERVAL_4_HOURS)
    s1d = get_tv_signal(tv_symbol, Interval.INTERVAL_1_DAY)
    signals = [s1, s4, s1d]

    # Combine
    decision = "HOLD"
    if signals.count("BUY") >= 2:
        decision = "BUY"
    elif signals.count("SELL") >= 2:
        decision = "SELL"

    pattern = detect_candlestick_pattern(price_history[tv_symbol])
    reason = f"MultiTF {signals} + pattern {pattern}"

    entry, sl, tp1, tp2 = compute_levels(price, decision)
    expected_profit = 0.0
    if decision == "BUY":
        expected_profit = (tp1 - entry) / entry * 100
    elif decision == "SELL":
        expected_profit = (entry - tp1) / entry * 100

    jakarta_time = datetime.now(timezone.utc) + JAKARTA_OFFSET

    # HOLD alert
    if decision == "HOLD":
        if hold_start_time[tv_symbol] is None:
            hold_start_time[tv_symbol] = jakarta_time
        else:
            elapsed = (jakarta_time - hold_start_time[tv_symbol]).total_seconds()
            if elapsed >= HOLD_ALERT_INTERVAL:
                send_telegram(f"⚠️ <b>{tv_symbol}</b> has been on HOLD for {int(elapsed/60)} mins.")
                hold_start_time[tv_symbol] = jakarta_time
    else:
        hold_start_time[tv_symbol] = None

    # If signal changed -> open new simulated trade (if BUY/SELL)
    if last_signals[tv_symbol] != decision:
        last_signals[tv_symbol] = decision
        if decision in ("BUY", "SELL"):
            # open trade only if none active for this symbol
            if tv_symbol not in active_trades:
                trade = open_trade(tv_symbol, coin_symbol, decision, entry, sl, tp1, tp2)
                msg = (
                    f"🚀 <b>Market Alert</b>\n"
                    f"⏰ <b>{jakarta_time.strftime('%Y-%m-%d %H:%M:%S')} WIB</b>\n"
                    f"💹 <b>{tv_symbol} ({coin_symbol})</b>\n"
                    f"💰 Price: {price} {CURRENCY.upper()}\n"
                    f"📈 Decision: <b>{decision}</b>\n"
                    f"📝 Reason: {reason}\n"
                    f"⚡ TP/SL: Entry {entry}, SL {sl}, TP1 {tp1}, TP2 {tp2}\n"
                    f"💵 Expected Profit (TP1): {expected_profit:.2f}%\n"
                )
                chart_file = generate_chart(tv_symbol, price_history[tv_symbol], entry, sl, tp1, tp2,
                                            signals=[(len(price_history[tv_symbol]) - 1, decision)])
                send_telegram(msg)
                send_telegram_image(chart_file, caption=f"{tv_symbol} chart")
            else:
                logging.info("Signal %s for %s but trade already active", decision, tv_symbol)
        else:
            # changed to HOLD -> if a trade is active we keep it open until evaluation window
            logging.info("%s changed to HOLD", tv_symbol)

    # If there's an active trade, evaluate TP/SL hits and expiration
    if tv_symbol in active_trades:
        trade = active_trades[tv_symbol]
        side = trade["side"]
        entry_price = trade["entry_price"]
        slp, tp1p, tp2p = trade["sl"], trade["tp1"], trade["tp2"]

        # check TP/SL
        hit = None
        if side == "BUY":
            if price >= tp2p:
                hit = ("TP2", tp2p)
            elif price >= tp1p:
                hit = ("TP1", tp1p)
            elif price <= slp:
                hit = ("SL", slp)
        else:  # SELL
            if price <= tp2p:
                hit = ("TP2", tp2p)
            elif price <= tp1p:
                hit = ("TP1", tp1p)
            elif price >= slp:
                hit = ("SL", slp)

        if hit:
            reason, exit_price = hit
            close_trade(tv_symbol, exit_price, reason)
        else:
            # check expiration (10 min)
            elapsed = (datetime.now(timezone.utc) - trade["start_time"]).total_seconds()
            if elapsed >= TRADE_EVAL_SECONDS:
                # mark as expired -> close at current price as unrealized evaluation
                close_trade(tv_symbol, price, "TIME_EXPIRED")
            else:
                # optionally send partial target alerts (first time)
                if not active_targets[tv_symbol]["tp1_sent"] and ((side == "BUY" and price >= tp1p) or (side == "SELL" and price <= tp1p)):
                    # mark and send
                    active_targets[tv_symbol]["tp1_sent"] = True
                    send_telegram(f"🔔 <b>{tv_symbol}</b> reached TP1 level ({tp1p}).")
                if not active_targets[tv_symbol]["tp2_sent"] and ((side == "BUY" and price >= tp2p) or (side == "SELL" and price <= tp2p)):
                    active_targets[tv_symbol]["tp2_sent"] = True
                    send_telegram(f"🔔 <b>{tv_symbol}</b> reached TP2 level ({tp2p}).")

    else:
        logging.debug("%s no active trade", tv_symbol)

    # write simple activity log (not trade)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            jakarta_time.strftime("%Y-%m-%d %H:%M:%S"),
            tv_symbol,
            coin_symbol,
            price,
            decision,
            reason,
            f"{expected_profit:.6f}",
            False,  # tp_hit placeholder
            False   # sl_hit placeholder
        ])

# -------------------------
# Dashboard summary
# -------------------------
def send_dashboard():
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for tv in TV_SYMBOLS:
        s = last_signals.get(tv, "HOLD") or "HOLD"
        counts[s] = counts.get(s, 0) + 1
    jakarta_time = datetime.now(timezone.utc) + JAKARTA_OFFSET
    msg = (
        f"📊 <b>Market Dashboard</b>\n"
        f"⏰ {jakarta_time.strftime('%Y-%m-%d %H:%M:%S')} WIB\n"
        f"BUY: <b>{counts['BUY']}</b>  SELL: <b>{counts['SELL']}</b>  HOLD: <b>{counts['HOLD']}</b>\n"
        f"Active trades: <b>{len(active_trades)}</b>"
    )
    send_telegram(msg)

# -------------------------
# Main loop
# -------------------------
def main():
    logging.info("Pro Trader Bot (live-profit) started.")
    last_dashboard = time.time()
    while True:
        for coin, tv in zip(SYMBOLS, TV_SYMBOLS):
            try:
                analyze_symbol(coin, tv)
            except Exception as e:
                logging.exception("Error analyzing %s: %s", tv, e)
        # periodic dashboard
        if time.time() - last_dashboard >= DASHBOARD_INTERVAL:
            try:
                send_dashboard()
            except Exception as e:
                logging.exception("Dashboard send failed: %s", e)
            last_dashboard = time.time()
        time.sleep(SLEEP_TIME)

if __name__ == "__main__":
    main()
