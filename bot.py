import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
import time

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# ==============================
# LOAD SP500
# ==============================
def load_sp500():
    df = pd.read_csv("https://datahub.io/core/s-and-p-500-companies/r/constituents.csv")
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()

# ==============================
# GET DATA (robuste)
# ==============================
def get_data(ticker):
    end = datetime.today()
    start = end - timedelta(days=150)

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start.date()}/{end.date()}?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"

    try:
        r = requests.get(url, timeout=10)
        data = r.json()

        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.set_index("Date", inplace=True)
        df = df.rename(columns={"c": "close"})

        return df[["close"]]

    except:
        return None

# ==============================
# INDICATORS
# ==============================
def add_indicators(df):
    df["EMA20"] = df["close"].ewm(span=20).mean()
    df["EMA50"] = df["close"].ewm(span=50).mean()
    df["EMA200"] = df["close"].ewm(span=200).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# ==============================
# PULLBACK CHECK
# ==============================
def is_pullback(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = latest["close"]

    # Trend
    if not (price > latest["EMA50"] > latest["EMA200"]):
        return False

    # Pullback zone
    if abs(price - latest["EMA20"]) / latest["EMA20"] > 0.03:
        return False

    # RSI sain
    if not (40 < latest["RSI"] < 60):
        return False

    # Rebond (bougie verte)
    if not (latest["close"] > prev["close"]):
        return False

    # Anti crash (évite couteaux qui tombent)
    if latest["close"] < df["close"].iloc[-10]:
        return False

    return True

# ==============================
# SCORE SIMPLE
# ==============================
def score(df):
    latest = df.iloc[-1]

    dist = abs(latest["close"] - latest["EMA20"]) / latest["EMA20"]
    rsi = latest["RSI"]

    s = 0

    # Proximité EMA20
    if dist < 0.01:
        s += 3
    elif dist < 0.02:
        s += 2
    else:
        s += 1

    # RSI optimal
    if 45 < rsi < 55:
        s += 3
    else:
        s += 1

    # Momentum court terme
    if df["close"].iloc[-1] > df["close"].iloc[-3]:
        s += 2

    return s

# ==============================
# SCAN
# ==============================
def scan():
    tickers = load_sp500()
    results = []

    for ticker in tickers:
        df = get_data(ticker)
        if df is None or len(df) < 100:
            continue

        df = add_indicators(df)

        if is_pullback(df):
            s = score(df)

            results.append({
                "ticker": ticker,
                "price": round(df["close"].iloc[-1], 2),
                "score": s
            })

        # évite limite API
        time.sleep(0.2)

    df_res = pd.DataFrame(results)

    if df_res.empty:
        return None

    return df_res.sort_values("score", ascending=False).head(10)

# ==============================
# DISCORD
# ==============================
def send_discord(df):
    if df is None:
        msg = "⚠️ Aucun pullback propre aujourd’hui"
    else:
        msg = "🟢 TEA PULLBACK CLEAN\n\n"

        for _, row in df.iterrows():
            msg += f"{row['ticker']} | {row['price']}$ | Score: {row['score']}\n"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
    except:
        print("Erreur envoi Discord")

# ==============================
# MAIN
# ==============================
def main():
    print("🔎 Scan en cours...")
    df = scan()

    if df is not None:
        print(df)

    send_discord(df)
    print("✅ Terminé")

if __name__ == "__main__":
    main()
