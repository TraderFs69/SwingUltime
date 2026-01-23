import streamlit as st
import pandas as pd
import numpy as np
import requests

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(layout="wide")
POLYGON_KEY = st.secrets["POLYGON_API_KEY"]

LOOKBACK = 140
TOP_N = 20

W_S1, W_S2, W_S3, W_S4 = 0.30, 0.25, 0.25, 0.20

# =====================================================
# LOAD TICKERS (ROBUSTE)
# =====================================================
@st.cache_data
def load_tickers():
    try:
        df = pd.read_excel("russell3000_constituents.xlsx", usecols=["Symbol"])
        s = df["Symbol"]
    except:
        df = pd.read_excel(
            "russell3000_constituents.xlsx",
            header=None,
            usecols=[0]
        )
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
# POLYGON OHLC
# =====================================================
@st.cache_data(ttl=3600)
def get_ohlc(ticker):
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{LOOKBACK}/2025-01-01"
        f"?adjusted=true&sort=asc&apiKey={POLYGON_KEY}"
    )
    r = requests.get(url, timeout=10).json()
    if "results" not in r:
        return None

    df = pd.DataFrame(r["results"])
    df["Close"] = df["c"]
    return df

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
# STRATÉGIE 1
# =====================================================
def strategy1(df):
    c = df["Close"]
    ema20, ema50 = EMA(c,20), EMA(c,50)
    roc5, roc10 = ROC(c,5), ROC(c,10)
    rsi = RSI(c)
    i = -1

    if len(df) < 60:
        return 0

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

    return round((p + v + a) / 12 * 100, 2)

# =====================================================
# STRATÉGIE 2
# =====================================================
def strategy2(df):
    c = df["Close"]
    ema50, ema100, ema200 = EMA(c,50), EMA(c,100), EMA(c,200)
    i = -1

    if len(df) < 200:
        return 0

    s = sum([
        c.iloc[i] > ema200.iloc[i],
        ema50.iloc[i] > ema100.iloc[i] > ema200.iloc[i],
        ema200.iloc[i] > ema200.iloc[i-20],
        EMA(c,20).iloc[i] > EMA(c,20).iloc[i-10]
    ])

    return round(s / 4 * 100, 2)

# =====================================================
# STRATÉGIE 3
# =====================================================
def strategy3(df):
    c = df["Close"]
    ema20 = EMA(c,20)
    atr = ATR(df)
    i = -1

    if len(df) < 40:
        return 0

    s = sum([
        atr.iloc[i] / c.iloc[i] < 0.04,
        c.iloc[i] > ema20.iloc[i],
        (c.iloc[i] - ema20.iloc[i]) / c.iloc[i] < 0.05,
        atr.iloc[i] < atr.rolling(40).quantile(0.6).iloc[i]
    ])

    return round(s / 4 * 100, 2)

# =====================================================
# STRATÉGIE 4
# =====================================================
def strategy4(df):
    i = -1
    close = df["Close"]
    rv = df["v"] / df["v"].rolling(20).mean()
    gap = (df["o"] - df["Close"].shift()) / df["Close"].shift() * 100

    if len(df) < 60:
        return 0

    score = sum([
        rv.iloc[i] > 1.3,
        rv.iloc[i] > 1.6,
        close.iloc[i] > close.rolling(20).max().iloc[i],
        close.iloc[i] > close.rolling(50).max().iloc[i],
        gap.tail(10).max() > 2,
        gap.tail(10).max() > 4
    ])

    return round(score / 6 * 100, 2)

# =====================================================
# RISK FLAG
# =====================================================
def risk_flag(df):
    flags = []
    c = df["Close"]
    ema20 = EMA(c,20)
    atr = ATR(df)
    i = -1

    if atr.iloc[i] / c.iloc[i] > 0.06:
        flags.append("High ATR")

    gap = (df["o"] - df["Close"].shift()) / df["Close"].shift() * 100
    if gap.tail(5).abs().max() > 5:
        flags.append("Gap")

    if (c.iloc[i] - ema20.iloc[i]) / ema20.iloc[i] > 0.08:
        flags.append("Extended")

    return flags

# =====================================================
# POSITION SIZE
# =====================================================
def position_size(score, flags):
    if score < 60:
        return 0.0

    base = 1.0 if score >= 80 else 0.75 if score >= 70 else 0.5
    mult = 1.0

    for f in flags:
        mult *= {
            "High ATR": 0.7,
            "Gap": 0.75,
            "Extended": 0.8
        }.get(f, 1)

    return round(min(base * mult, 1.0) * 100, 1)

# =====================================================
# SCAN
# =====================================================
def scan_universe(tickers):
    rows = []

    for t in tickers:
        df = get_ohlc(t)
        if df is None or len(df) < 60:
            continue

        s1 = strategy1(df)
        s2 = strategy2(df)
        s3 = strategy3(df)
        s4 = strategy4(df)

        total = round(
            W_S1*s1 + W_S2*s2 + W_S3*s3 + W_S4*s4,
            2
        )

        flags = risk_flag(df)
        size = position_size(total, flags)

        rows.append([
            t, s1, s2, s3, s4, total,
            " | ".join(flags) if flags else "—",
            size
        ])

    return pd.DataFrame(rows, columns=[
        "Ticker","S1","S2","S3","S4",
        "Score Global","Risk Flag","Position Size (%)"
    ])

# =====================================================
# UI
# =====================================================
st.title("📊 Swing Scanner — Score, Risk & Position Size")

limit = st.slider("Nombre de tickers à scanner", 50, len(TICKERS), 300)

if st.button("Lancer le scan"):
    with st.spinner("Scan en cours…"):
        df = scan_universe(TICKERS[:limit])
        df = df.sort_values("Score Global", ascending=False).head(TOP_N)
        st.dataframe(df, width="stretch")
