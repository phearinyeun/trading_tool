# pro_trader_bot_futures_pro.py
import os, csv, logging, asyncio
from datetime import datetime, timezone, timedelta
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import pandas as pd
import numpy as np
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
CANDLE_HISTORY = int(os.getenv("CANDLE_HISTORY", 100))
TRADE_EVAL_SECONDS = int(os.getenv("TRADE_EVAL_SECONDS", 600))
DASHBOARD_INTERVAL = int(os.getenv("DASHBOARD_INTERVAL", 1800))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", 0.02))  # 2% risk
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 1000))
LEVERAGE = float(os.getenv("LEVERAGE", 5))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))

JAKARTA_OFFSET = timedelta(hours=7)

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG_FILE = "alerts_log.csv"
TRADES_FILE = "trades_log.csv"

# -------------------------
# State
# -------------------------
last_signals = {tv: None for tv in TV_SYMBOLS}
price_history = {tv: deque(maxlen=CANDLE_HISTORY) for tv in TV_SYMBOLS}
active_trades = {}
active_targets = {tv: {"tp1_sent": False, "tp2_sent": False, "sl_sent": False} for tv in TV_SYMBOLS}

executor = ThreadPoolExecutor(max_workers=5)

# -------------------------
# Telegram functions
# -------------------------
async def send_telegram(session, msg):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with session.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10) as r:
            if r.status != 200:
                logging.error("Telegram send failed: %s %s", r.status, await r.text())
    except Exception as e:
        logging.exception("Telegram send error: %s", e)

# -------------------------
# TradingView signals
# -------------------------
def get_tv_signal_sync(tv_symbol, interval):
    try:
        handler = TA_Handler(symbol=tv_symbol, screener="crypto", exchange="BINANCE", interval=interval)
        analysis = handler.get_analysis()
        summary = getattr(analysis, "summary", {})
        return summary.get("RECOMMENDATION", "HOLD") if isinstance(summary, dict) else "HOLD"
    except Exception as e:
        logging.warning("TV error for %s: %s", tv_symbol, e)
        return "HOLD"

async def get_tv_signal(tv_symbol, interval):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, get_tv_signal_sync, tv_symbol, interval)

# -------------------------
# Technical indicator helpers
# -------------------------
def compute_levels(price, atr, signal):
    decimals = 2 if price>=100 else 4 if price>=1 else 6
    if signal=="BUY":
        sl = round(price - atr, decimals)
        tp1 = round(price + atr*1.5, decimals)
        tp2 = round(price + atr*3, decimals)
    elif signal=="SELL":
        sl = round(price + atr, decimals)
        tp1 = round(price - atr*1.5, decimals)
        tp2 = round(price - atr*3, decimals)
    else:
        sl=tp1=tp2=round(price, decimals)
    return round(price, decimals), sl, tp1, tp2

def calculate_atr(prices, period=ATR_PERIOD):
    if len(prices)<period: return 0.0
    highs = np.array(prices)
    lows = np.array(prices)
    close = np.array(prices)
    tr = np.maximum(highs[1:]-lows[1:], np.abs(highs[1:]-close[:-1]), np.abs(lows[1:]-close[:-1]))
    atr = np.mean(tr[-period:])
    return float(atr)

# -------------------------
# Trade management
# -------------------------
def open_trade(tv_symbol, coin_id, side, entry, sl, tp1, tp2):
    trade = {"start_time": datetime.now(timezone.utc), "tv_symbol": tv_symbol, "coin_id": coin_id,
             "side": side, "entry_price": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "status": "OPEN",
             "tp1_done": False, "tp2_done": False}
    active_trades[tv_symbol] = trade
    logging.info("Opened trade %s %s @ %s", tv_symbol, side, entry)
    return trade

def close_trade(tv_symbol, exit_price, exit_reason):
    trade = active_trades.get(tv_symbol)
    if not trade: return
    end_time = datetime.now(timezone.utc)
    duration = (end_time - trade["start_time"]).total_seconds()
    side = trade["side"]
    entry = trade["entry_price"]
    profit_pct = (exit_price-entry)/entry*100*LEVERAGE if side=="BUY" else (entry-exit_price)/entry*100*LEVERAGE
    with open(TRADES_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([trade["start_time"].strftime("%Y-%m-%d %H:%M:%S"), trade["tv_symbol"], trade["coin_id"],
                         side, entry, trade["sl"], trade["tp1"], trade["tp2"], end_time.strftime("%Y-%m-%d %H:%M:%S"),
                         exit_price, exit_reason, f"{profit_pct:.2f}", int(duration)])
    logging.info("Closed trade %s %s @ %s Profit: %.2f%%", tv_symbol, side, exit_price, profit_pct)
    active_trades.pop(tv_symbol, None)

# -------------------------
# Symbol analysis
# -------------------------
async def analyze_symbol(session, coin_symbol, tv_symbol):
    url = f"https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": coin_symbol, "vs_currencies": CURRENCY, "include_24hr_change": "true"}
    async with session.get(url, params=params) as r:
        data = await r.json()
    price = float(data[coin_symbol][CURRENCY])
    change_24h = data[coin_symbol].get(f"{CURRENCY}_24h_change",0.0)
    price_history[tv_symbol].append(price)

    s15,s1h,s4h = await asyncio.gather(
        get_tv_signal(tv_symbol, Interval.INTERVAL_15_MINUTES),
        get_tv_signal(tv_symbol, Interval.INTERVAL_1_HOUR),
        get_tv_signal(tv_symbol, Interval.INTERVAL_4_HOURS)
    )
    signals = [s.upper() for s in [s15,s1h,s4h]]
    decision = "HOLD"
    if signals.count("BUY")>=2: decision="BUY"
    elif signals.count("SELL")>=2: decision="SELL"

    atr = calculate_atr(list(price_history[tv_symbol]))
    entry, sl, tp1, tp2 = compute_levels(price, atr, decision)

    suggested_exit = "HOLD"
    if last_signals[tv_symbol]!=decision:
        last_signals[tv_symbol]=decision
        if decision in ["BUY","SELL"]:
            open_trade(tv_symbol, coin_symbol, decision, entry, sl, tp1, tp2)
            suggested_exit = decision

    # Monitor active trades
    if tv_symbol in active_trades:
        trade = active_trades[tv_symbol]
        side = trade["side"]
        slp,tp1p,tp2p = trade["sl"], trade["tp1"], trade["tp2"]

        if not trade["tp1_done"] and ((side=="BUY" and price>=tp1p) or (side=="SELL" and price<=tp1p)):
            trade["tp1_done"]=True
            await send_telegram(session,f"🔔 {tv_symbol} hit TP1 ({tp1p}) — close 50% position")

        if not trade["tp2_done"] and ((side=="BUY" and price>=tp2p) or (side=="SELL" and price<=tp2p)):
            trade["tp2_done"]=True
            close_trade(tv_symbol,tp2p,"TP2")
            await send_telegram(session,f"✅ {tv_symbol} hit TP2 ({tp2p}) — trade closed")

        if (side=="BUY" and price<=slp) or (side=="SELL" and price>=slp):
            close_trade(tv_symbol,price,"SL")
            await send_telegram(session,f"❌ {tv_symbol} hit Stop Loss ({slp}) — trade closed")

    # Send alert
    jakarta_time = datetime.now(timezone.utc)+JAKARTA_OFFSET
    msg = (
        f"🚀 Market Alert 🚀\n"
        f"⏰ {jakarta_time.strftime('%Y-%m-%d %H:%M:%S')} WIB\n"
        f"💹 Symbol: {tv_symbol} ({coin_symbol})\n"
        f"💰 Price: {price:.2f} {CURRENCY.upper()}\n"
        f"📊 24h Change: {change_24h:.2f}%\n"
        f"🧠 TA Signal: {signals}\n"
        f"📈 Decision: {decision}\n"
        f"⚡️ Levels:\n"
        f"    Entry: {entry}\n"
        f"    Stop Loss: {sl}\n"
        f"    TP1: {tp1}\n"
        f"    TP2: {tp2}\n"
        f"⏹️ Suggested Exit: {suggested_exit}"
    )
    await send_telegram(session,msg)

# -------------------------
# Dashboard
# -------------------------
async def send_dashboard(session):
    msg="📊 Dashboard\n"
    for tv,trade in active_trades.items():
        current_price = price_history[tv][-1] if price_history[tv] else trade["entry_price"]
        side = trade["side"]
        entry = trade["entry_price"]
        profit_pct = ((current_price-entry)/entry*100*LEVERAGE) if side=="BUY" else ((entry-current_price)/entry*100*LEVERAGE)
        msg+=f"{tv}: {side} Entry {entry} Current {current_price:.2f} Profit {profit_pct:.2f}%\n"
    await send_telegram(session,msg)

# -------------------------
# Main loop
# -------------------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        last_dashboard = asyncio.get_event_loop().time()
        while True:
            tasks = [analyze_symbol(session, coin, tv) for coin,tv in zip(SYMBOLS,TV_SYMBOLS)]
            await asyncio.gather(*tasks)
            if asyncio.get_event_loop().time()-last_dashboard>=DASHBOARD_INTERVAL:
                await send_dashboard(session)
                last_dashboard = asyncio.get_event_loop().time()
            await asyncio.sleep(SLEEP_TIME)

if __name__=="__main__":
    asyncio.run(main_loop())
