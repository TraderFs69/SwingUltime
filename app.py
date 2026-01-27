import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import date, timedelta

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(layout="wide")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets.get("DISCORD_WEBHOOK_URL")

LOOKBACK = 160
TOP_N = 15

W_S1, W_S2, W_S3, W_S4 = 0.30, 0.25, 0.25, 0.20

# =====================================================
# LOAD TICKERS
# =====================================================
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    s = df.iloc[:, 0]
    tickers = (
        s.dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .unique()
        .tolist()
    )
    return [t for t in tickers if t != "SYMBOL"]

TICKERS = load_tickers()

# =====================================================
# POLYGON OHLC — DATES DYNAMIQUES
# =====================================================
@st.cache_data(ttl=3600)
def get_ohlc(ticker):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if "results" not in data or not data["results"]:
            return None

        df = pd.DataFrame(data["results"])
        df["Close"] = df["c"]
        return df
    except Exception:
        return None

# =====================================================
# INDICATEURS
# =====================================================
def EMA(s, n): return s.ewm(span=n, adjust=False).mean()
def ROC(s, n): return s.pct_change(n) * 100

def RSI(s, n=14):
    d = s.diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    rs = g.rolling(n).mean() / l.rolling(n).mean()
    return 100 - (100 / (1 + rs))

def ATR(df, n=14):
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - df["Close"].shift()).abs(),
        (df["l"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# =====================================================
# STRATÉGIES
# =====================================================
def strategy1(df):
    if len(df) < 60: return 0
    c = df["Close"]
    ema20, ema50 = EMA(c,20), EMA(c,50)
    roc5, roc10 = ROC(c,5), ROC(c,10)
    rsi = RSI(c)
    i = -1

    p = sum([
        c.iloc[i] > ema50.iloc[i],
        c.iloc[i] > ema20.iloc[i],
        ema20.iloc[i] > ema50.iloc[i],
        (c.iloc[i] - c.rolling(20).min().iloc[i]) /
        (c.rolling(20).max().iloc[i] - c.rolling(20).min().iloc[i]) > 0.5
    ])

    v = sum([
        roc5.iloc[i] > 0,
        roc10.iloc[i] > 0,
        ema20.iloc[i] - ema20.iloc[i-5] > 0,
        50 < rsi.iloc[i] < 65
    ])

    a = sum([
        roc5.iloc[i] > roc10.iloc[i],
        rsi.iloc[i] - rsi.iloc[i-5] > 0,
        (ema20.iloc[i] - ema50.iloc[i]) >
        (ema20.iloc[i-5] - ema50.iloc[i-5]),
        ROC(ROC(c,5),5).iloc[i] > 0
    ])

    return round((p+v+a)/12*100,2)

def strategy2(df):
    if len(df) < 200: return 0
    c = df["Close"]
    ema50, ema100, ema200 = EMA(c,50), EMA(c,100), EMA(c,200)
    i = -1
    s = sum([
        c.iloc[i] > ema200.iloc[i],
        ema50.iloc[i] > ema100.iloc[i] > ema200.iloc[i],
        ema200.iloc[i] > ema200.iloc[i-20],
        EMA(c,20).iloc[i] > EMA(c,20).iloc[i-10]
    ])
    return round(s/4*100,2)

def strategy3(df):
    if len(df) < 40: return 0
    c = df["Close"]
    ema20 = EMA(c,20)
    atr = ATR(df)
    i = -1
    s = sum([
        atr.iloc[i] / c.iloc[i] < 0.04,
        c.iloc[i] > ema20.iloc[i],
        (c.iloc[i] - ema20.iloc[i]) / c.iloc[i] < 0.05,
        atr.iloc[i] < atr.rolling(40).quantile(0.6).iloc[i]
    ])
    return round(s/4*100,2)

def strategy4(df):
    if len(df) < 60: return 0
    close = df["Close"]
    rv = df["v"] / df["v"].rolling(20).mean()
    gap = (df["o"] - df["Close"].shift()) / df["Close"].shift() * 100
    i = -1
    s = sum([
        rv.iloc[i] > 1.3,
        rv.iloc[i] > 1.6,
        close.iloc[i] > close.rolling(20).max().iloc[i],
        close.iloc[i] > close.rolling(50).max().iloc[i],
        gap.tail(10).max() > 2,
        gap.tail(10).max() > 4
    ])
    return round(s/6*100,2)

# =====================================================
# SCORE GLOBAL (OFFSET)
# =====================================================
def compute_score(df, offset):
    df = df.iloc[:offset]
    s1, s2 = strategy1(df), strategy2(df)
    s3, s4 = strategy3(df), strategy4(df)
    return round(W_S1*s1 + W_S2*s2 + W_S3*s3 + W_S4*s4, 2)

# =====================================================
# SCAN — NOUVEAUX ENTRANTS UNIQUEMENT
# =====================================================
def scan_universe(tickers):
    today, yesterday = [], []

    for t in tickers:
        df = get_ohlc(t)
        if df is None or len(df) < 200:
            continue

        price = round(df["Close"].iloc[-1], 2)
        score_today = compute_score(df, -1)
        score_yesterday = compute_score(df, -2)

        today.append([t, price, score_today])
        yesterday.append([t, score_yesterday])

    df_today = pd.DataFrame(today, columns=["Ticker","Price","Score"])
    df_yesterday = pd.DataFrame(yesterday, columns=["Ticker","Score_Y"])

    top_today = df_today.sort_values("Score", ascending=False).head(TOP_N)
    top_yesterday = df_yesterday.sort_values("Score_Y", ascending=False).head(TOP_N)

    new_entries = top_today[
        ~top_today["Ticker"].isin(top_yesterday["Ticker"])
    ]

    return new_entries.sort_values("Score", ascending=False)

# =====================================================
# DISCORD
# =====================================================
def send_to_discord(df):
    if not DISCORD_WEBHOOK or df.empty:
        return

    lines = [
        f"🚀 **{r['Ticker']}** @ ${r['Price']} | Score `{r['Score']}`"
        for _, r in df.iterrows()
    ]

    payload = {
        "content": "🚨 **NOUVEAUX ENTRANTS — Swing Scanner**\n\n" +
                   "\n".join(lines)
    }

    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
    except Exception:
        pass

# =====================================================
# UI
# =====================================================
st.title("🚨 Swing Scanner — Nouveaux entrants seulement")

limit = st.slider("Nombre de tickers à scanner", 50, len(TICKERS), 300)

if st.button("🚀 Lancer le scan et envoyer sur Discord"):
    with st.spinner("Scan en cours…"):
        df = scan_universe(TICKERS[:limit])

        if not df.empty:
            st.dataframe(df, use_container_width=True)
            send_to_discord(df)
            st.success("🚀 Nouveaux entrants envoyés sur Discord")
        else:
            st.info("Aucun nouveau titre dans le TOP aujourd’hui.")
