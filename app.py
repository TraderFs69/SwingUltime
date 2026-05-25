import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

LOOKBACK_DAYS = 320
MAX_TICKERS = 3000
REQUEST_SLEEP = 0.08

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TEA-Pullback-Bot/1.0",
    "Accept-Encoding": "gzip",
})

# ==============================
# LOAD RUSSELL 3000
# ==============================
def load_universe():
    df = pd.read_excel("russell3000_constituents.xlsx")

    tickers = (
        df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.upper()
        .str.strip()
        .unique()
        .tolist()
    )

    tickers = [
        t.replace(".", "-")
        for t in tickers
        if t and t != "SYMBOL"
    ]

    print(f"✅ {len(tickers)} tickers chargés")
    return tickers[:MAX_TICKERS]

# ==============================
# GET DATA
# ==============================
def get_data(ticker, retries=3):
    end = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.date()}/{end.date()}"
    )

    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": POLYGON_API_KEY,
    }

    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=(5, 25))

            if r.status_code != 200:
                time.sleep(0.4)
                continue

            data = r.json()
            if not data.get("results"):
                return None

            df = pd.DataFrame(data["results"])
            df["Date"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("Date", inplace=True)

            df = df.rename(columns={
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            })

            return df[["open", "high", "low", "close", "volume"]]

        except requests.exceptions.RequestException as e:
            print(f"⚠️ {ticker} tentative {attempt + 1}: {e}")
            time.sleep(0.5 * (attempt + 1))

    return None

# ==============================
# INDICATORS
# ==============================
def add_indicators(df):
    df = df.copy()

    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# ==============================
# PULLBACK CHECK
# ==============================
def is_pullback(df):
    if len(df) < 220:
        return False

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    required = ["close", "EMA20", "EMA50", "EMA200", "RSI"]
    if latest[required].isna().any():
        return False

    price = latest["close"]

    # Trend
    if not (price > latest["EMA50"] > latest["EMA200"]):
        return False

    # Pullback près EMA20
    if abs(price - latest["EMA20"]) / latest["EMA20"] > 0.03:
        return False

    # RSI zone propre
    if not (40 < latest["RSI"] < 60):
        return False

    # Rebond
    if not (latest["close"] > prev["close"]):
        return False

    # Anti-crash
    if latest["close"] < df["close"].iloc[-10]:
        return False

    return True

# ==============================
# SCORE
# ==============================
def score(df):
    latest = df.iloc[-1]

    dist = abs(latest["close"] - latest["EMA20"]) / latest["EMA20"]
    rsi = latest["RSI"]

    s = 0

    if dist < 0.01:
        s += 3
    elif dist < 0.02:
        s += 2
    else:
        s += 1

    if 45 < rsi < 55:
        s += 3
    else:
        s += 1

    if df["close"].iloc[-1] > df["close"].iloc[-3]:
        s += 2

    return s

# ==============================
# SCAN
# ==============================
def scan():
    tickers = load_universe()
    results = []

    for i, ticker in enumerate(tickers, 1):
        print(f"🔎 {i}/{len(tickers)} — {ticker}")

        df = get_data(ticker)
        time.sleep(REQUEST_SLEEP)

        if df is None or len(df) < 220:
            continue

        df = add_indicators(df)

        if is_pullback(df):
            s = score(df)

            results.append({
                "ticker": ticker,
                "price": round(df["close"].iloc[-1], 2),
                "score": s,
                "rsi": round(df["RSI"].iloc[-1], 1),
                "ema20": round(df["EMA20"].iloc[-1], 2),
            })

    df_res = pd.DataFrame(results)

    if df_res.empty:
        return None

    return df_res.sort_values("score", ascending=False).head(10)

# ==============================
# DISCORD
# ==============================
def send_discord(df):
    if not DISCORD_WEBHOOK_URL:
        print("❌ DISCORD_WEBHOOK_URL manquant")
        return

    if df is None:
        msg = "⚠️ Aucun pullback propre aujourd’hui."
    else:
        msg = "🟢 **TEA PULLBACK CLEAN — TOP 10**\n\n"

        for _, row in df.iterrows():
            msg += (
                f"**{row['ticker']}** | "
                f"${row['price']} | "
                f"Score: {row['score']} | "
                f"RSI: {row['rsi']} | "
                f"EMA20: {row['ema20']}\n"
            )

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
        print("✅ Message Discord envoyé")
    except Exception as e:
        print("❌ Erreur Discord:", e)

# ==============================
# MAIN
# ==============================
def main():
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY manquant")

    print("====================")
    print("STARTING TEA PULLBACK CLEAN")
    print("====================")

    df = scan()
    send_discord(df)

if __name__ == "__main__":
    main()
