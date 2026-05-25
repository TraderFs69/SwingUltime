import pandas as pd
import requests
import os
import time
from datetime import datetime, timedelta

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

MAX_TICKERS = 150
LOOKBACK_DAYS = 160
REQUEST_SLEEP = 0.08

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TEA-Elite-Recap/1.0",
    "Accept-Encoding": "gzip",
})

# -----------------------------
# LOAD RUSSELL 3000
# -----------------------------
def load_sp500():
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

    print(f"✅ {len(tickers)} tickers chargés depuis russell3000_constituents.xlsx")
    return tickers

# -----------------------------
# FETCH DATA POLYGON
# -----------------------------
def get_data(ticker, start, end, retries=3):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"

    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 5000,
        "apiKey": POLYGON_API_KEY,
    }

    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=(5, 25))

            if r.status_code != 200:
                print(f"⚠️ {ticker} status {r.status_code}")
                time.sleep(0.4 * (attempt + 1))
                continue

            data = r.json()

            if not data.get("results"):
                return None

            df = pd.DataFrame(data["results"])
            df["Date"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("Date", inplace=True)

            return df

        except requests.exceptions.RequestException as e:
            print(f"⚠️ {ticker} tentative {attempt + 1}: {e}")
            time.sleep(0.5 * (attempt + 1))

    return None

# -----------------------------
# INDICATORS
# -----------------------------
def compute_indicators(df):
    df = df.copy()

    df["EMA9"] = df["c"].ewm(span=9, adjust=False).mean()
    df["EMA20"] = df["c"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["c"].ewm(span=50, adjust=False).mean()

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

    if last["EMA9"] > last["EMA20"]:
        score += 2

    if last["EMA20"] > last["EMA50"]:
        score += 2

    if last["RET"] > 0:
        score += 2

    vol_mean = df["VOL"].rolling(20).mean().iloc[-1]
    if pd.notna(vol_mean) and last["VOL"] > vol_mean:
        score += 2

    if prev["c"] < prev["EMA9"] and last["c"] > last["EMA9"]:
        score += 2

    return score

# -----------------------------
# SCAN
# -----------------------------
def scan_market():
    tickers = load_sp500()

    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)

    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")

    results = []

    for i, t in enumerate(tickers[:MAX_TICKERS], 1):
        print(f"🔎 {i}/{min(MAX_TICKERS, len(tickers))} — {t}")

        df = get_data(t, start, end)
        time.sleep(REQUEST_SLEEP)

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
    if not DISCORD_WEBHOOK_URL:
        print("❌ DISCORD_WEBHOOK_URL manquant")
        return

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10
        )

        if r.status_code not in [200, 204]:
            print(f"⚠️ Discord status: {r.status_code} | {r.text}")

    except Exception as e:
        print("❌ Erreur Discord:", e)

# -----------------------------
# BUILD REPORT
# -----------------------------
def build_report(df):
    report = "🟫 **TEA ELITE RECAP**\n\n"

    for _, row in df.iterrows():
        report += (
            f"**{row['ticker']}** | "
            f"Score: {row['score']} | "
            f"Price: ${round(row['price'], 2)}\n"
        )

    report += "\n🧠 Lecture rapide:\nMomentum + structure propre.\n"

    return report

# -----------------------------
# MAIN
# -----------------------------
def main():
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY manquant")

    print("====================")
    print("STARTING TEA ELITE RECAP")
    print("====================")
    print("🔄 Scan en cours...")

    df = scan_market()

    print("Résultats trouvés:", len(df))

    if df.empty:
        message = "⚠️ Aucun setup valide aujourd’hui — marché faible ou données non prêtes."
    else:
        message = build_report(df)

    send_discord(message)

    print("✅ Envoyé sur Discord")

if __name__ == "__main__":
    main()
