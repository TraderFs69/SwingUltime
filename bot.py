import pandas as pd
import requests
import os
from datetime import datetime, timedelta

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# -----------------------------
# LOAD SP500
# -----------------------------
def load_sp500():
    url = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
    df = pd.read_csv(url)
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()

# -----------------------------
# FETCH DATA POLYGON
# -----------------------------
def get_data(ticker, start, end):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=500&apiKey={POLYGON_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()

        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.set_index("Date", inplace=True)

        return df
    except:
        return None

# -----------------------------
# INDICATORS
# -----------------------------
def compute_indicators(df):
    df["EMA9"] = df["c"].ewm(span=9).mean()
    df["EMA20"] = df["c"].ewm(span=20).mean()
    df["EMA50"] = df["c"].ewm(span=50).mean()

    df["RET"] = df["c"].pct_change()
    df["VOL"] = df["v"]

    return df

# -----------------------------
# SCORE TEA
# -----------------------------
def compute_score(df):
    if len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    score = 0

    # Trend
    if last["EMA9"] > last["EMA20"]:
        score += 2
    if last["EMA20"] > last["EMA50"]:
        score += 2

    # Momentum
    if last["RET"] > 0:
        score += 2

    # Volume spike
    if last["VOL"] > df["VOL"].rolling(20).mean().iloc[-1]:
        score += 2

    # Pullback propre
    if prev["c"] < prev["EMA9"] and last["c"] > last["EMA9"]:
        score += 2

    return score

# -----------------------------
# SCAN
# -----------------------------
def scan_market():
    tickers = load_sp500()

    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=120)

    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")

    results = []

    for t in tickers[:150]:  # 🔥 rapide (tu peux augmenter)
        df = get_data(t, start, end)
        if df is None or len(df) < 50:
            continue

        df = compute_indicators(df)
        score = compute_score(df)

        if score is not None:
            results.append((t, score, df["c"].iloc[-1]))

    df_res = pd.DataFrame(results, columns=["ticker", "score", "price"])

    if df_res.empty:
        return df_res

    return df_res.sort_values("score", ascending=False).head(10)

# -----------------------------
# DISCORD
# -----------------------------
def send_discord(message):
    data = {"content": message}
    requests.post(DISCORD_WEBHOOK_URL, json=data)

# -----------------------------
# BUILD REPORT
# -----------------------------
def build_report(df):
    report = "🟫 TEA ELITE RECAP\n\n"

    for _, row in df.iterrows():
        report += f"{row['ticker']} | Score: {row['score']} | Price: {round(row['price'],2)}\n"

    report += "\n🧠 Lecture rapide:\nMomentum + structure propre.\n"

    return report

# -----------------------------
# MAIN
# -----------------------------
def main():
    print("🔄 Scan en cours...")

    df = scan_market()

    print("Résultats trouvés:", len(df))

    # 🔥 FALLBACK SI VIDE
    if df.empty:
        message = "⚠️ Aucun setup valide aujourd’hui — marché faible ou données non prêtes"
    else:
        message = build_report(df)

    send_discord(message)

    print("✅ Envoyé sur Discord")

if __name__ == "__main__":
    main()
