import os
import pickle
import pandas as pd
import numpy as np
import requests
from xgboost import XGBClassifier
import ta
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timedelta
import telegram

# ===============================
# 1️⃣ Load environment
# ===============================
load_dotenv()
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "").split(",")]
TV_SYMBOLS = [s.strip() for s in os.getenv("TV_SYMBOLS", "").split(",")]
CURRENCY = os.getenv("CURRENCY", "usd")
HIST_DAYS = int(os.getenv("HIST_DAYS", 60))
THRESHOLD = float(os.getenv("PRICE_CHANGE_THRESHOLD", 0.002))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = None
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# ===============================
# 2️⃣ Fetch historical daily data from CoinGecko
# ===============================
def fetch_coin_data(symbol):
    url = f"https://api.coingecko.com/api/v3/coins/{symbol}/market_chart"
    params = {"vs_currency": CURRENCY, "days": HIST_DAYS, "interval": "daily"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data["prices"], columns=["timestamp","close"])
        df["volume"] = [v[1] for v in data["total_volumes"]]
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df["open"] = df["close"].shift(1)
        df["high"] = df["close"]
        df["low"] = df["close"]
        df = df.dropna()
        return df[["timestamp","open","high","low","close","volume"]]
    except Exception as e:
        print(f"❌ Failed to fetch {symbol}: {e}")
        return None

# ===============================
# 3️⃣ Save CSV
# ===============================
def save_csv(df, tv_symbol):
    csv_file = DATA_DIR/f"{tv_symbol}.csv"
    df.to_csv(csv_file, index=False)
    print(f"✅ CSV saved: {csv_file}")
    return csv_file

# ===============================
# 4️⃣ Feature engineering
# ===============================
def generate_features(df):
    df = df.copy()
    df["MA20"] = df["close"].rolling(20).mean()
    df["MA50"] = df["close"].rolling(50).mean()
    df["MA200"] = df["close"].rolling(200).mean()
    df["RSI"] = ta.momentum.RSIIndicator(df["close"]).rsi()
    df["MACD"] = ta.trend.MACD(df["close"]).macd_diff()
    for i in range(1,4):
        df[f"diff{i}"] = df["close"].diff(i)
    df["pattern"] = 0
    for i in range(2,len(df)):
        if abs(df["close"].iloc[i]-df["close"].iloc[i-1])<0.001*df["close"].iloc[i]:
            df.at[i,"pattern"]=1  # Doji
        elif df["close"].iloc[i]>df["close"].iloc[i-1] and df["close"].iloc[i-1]<df["close"].iloc[i-2]:
            df.at[i,"pattern"]=2  # Bullish
        elif df["close"].iloc[i]<df["close"].iloc[i-1] and df["close"].iloc[i-1]>df["close"].iloc[i-2]:
            df.at[i,"pattern"]=3  # Bearish
    df = df.dropna()
    return df

# ===============================
# 5️⃣ Generate labels
# ===============================
def generate_labels(df, threshold=THRESHOLD):
    labels=[]
    prices=df["close"].values
    for i in range(len(prices)-1):
        change=(prices[i+1]-prices[i])/prices[i]
        if change>threshold:
            labels.append(1)   # BUY
        elif change<-threshold:
            labels.append(2)   # SELL
        else:
            labels.append(0)   # HOLD
    labels.append(0)
    return np.array(labels)

# ===============================
# 6️⃣ Train AI model
# ===============================
def train_ai_model(csv_file, coin_symbol):
    print(f"Training AI model for {coin_symbol}...")
    df = pd.read_csv(csv_file)
    df = generate_features(df)
    labels = generate_labels(df)
    X = df.drop(columns=["timestamp","open","high","low","close","volume"])
    model = XGBClassifier(use_label_encoder=False, eval_metric="mlogloss")
    model.fit(X, labels)
    filename = f"ai_model_{coin_symbol.upper()}.pkl"
    with open(filename,"wb") as f:
        pickle.dump(model,f)
    print(f"✅ AI model saved: {filename}")
    return model, X, labels

# ===============================
# 7️⃣ Predict latest signal & Telegram alert
# ===============================
def predict_and_alert(model, df_features, coin_symbol, tv_symbol):
    latest = df_features.tail(1).drop(columns=["timestamp","open","high","low","close","volume"])
    signal = model.predict(latest)[0]
    signal_str = {0:"HOLD", 1:"BUY", 2:"SELL"}[signal]
    msg = f"📊 {tv_symbol} Signal: {signal_str} (AI Prediction)\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    print(msg)
    if bot:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

# ===============================
# 8️⃣ Run all
# ===============================
for coin, tv in zip(SYMBOLS, TV_SYMBOLS):
    df = fetch_coin_data(coin)
    if df is None:
        continue
    csv_file = save_csv(df, tv)
    try:
        model, X, labels = train_ai_model(csv_file, coin)
        predict_and_alert(model, pd.read_csv(csv_file), coin, tv)
    except Exception as e:
        print(f"❌ Failed for {coin}: {e}")
