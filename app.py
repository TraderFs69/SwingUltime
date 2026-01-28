# =====================================================
# SWING SCANNER — RANKING & ACCÉLÉRATION (STABLE)
# =====================================================
import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, timedelta

# ---------------- CONFIG ----------------
st.set_page_config(layout="wide")
st.title("🚀 Swing Scanner — Ranking & Accélération")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]

LOOKBACK = 160
TOP_N = 30
DELTA_MIN = 5

# ---------------- SESSION HTTP ----------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TradingEnAction-Scanner/1.0"})

# ---------------- LOAD TICKERS ----------------
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    return (
        df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.upper()
        .unique()
        .tolist()
    )

TICKERS = load_tickers()

# ---------------- POLYGON OHLC ----------------
def get_ohlc(ticker, retries=2):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    )

    for _ in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                return None

            data = r.json()
            if not data.get("results"):
                return None

            df = pd.DataFrame(data["results"])
            df["Close"] = df["c"]
            return df

        except requests.exceptions.Timeout:
            time.sleep(0.5)

        except Exception:
            return None

    return None

# ---------------- INDICATEURS ----------------
def EMA(s, n): return s.ewm(span=n, adjust=False).mean()
def ROC(s, n): return s.pct_change(n) * 100

def compute_score(df):
    c = df["Close"]
    score = sum([
        c.iloc[-1] > EMA(c, 50).iloc[-1],
        EMA(c, 20).iloc[-1] > EMA(c, 20).iloc[-5],
        ROC(c, 5).iloc[-1] > 0,
        ROC(ROC(c, 5), 5).iloc[-1] > 0
    ])
    return round(score / 4 * 100, 2)

# ---------------- SCAN ----------------
def scan_universe(tickers):
    rows = []
    progress = st.progress(0)
    status = st.empty()

    for i, t in enumerate(tickers):
        status.write(f"{i+1}/{len(tickers)} — {t}")

        df = get_ohlc(t)
        if df is None or len(df) < 100:
            continue

        score_today = compute_score(df)
        score_week = compute_score(df.iloc[:-5])

        rows.append([
            t,
            round(df["Close"].iloc[-1], 2),
            score_today,
            round(score_today - score_week, 2)
        ])

        progress.progress((i + 1) / len(tickers))

    df_all = pd.DataFrame(rows, columns=["Ticker", "Price", "Score", "Delta"])

    return (
        df_all.sort_values("Score", ascending=False).head(TOP_N),
        df_all[df_all["Delta"] > DELTA_MIN].sort_values("Delta", ascending=False)
    )

# ---------------- UI ----------------
limit = st.slider("Nombre de tickers", 50, len(TICKERS), 200)

if st.button("🚀 Scanner Swing"):
    top, accel = scan_universe(TICKERS[:limit])

    st.subheader("🏆 TOP RANKING")
    st.dataframe(top, use_container_width=True)

    st.subheader("⚡ ACCÉLÉRATEURS")
    st.dataframe(accel, use_container_width=True)
