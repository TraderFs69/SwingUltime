# =====================================================
# SWING SCANNER — RANKING & ACCÉLÉRATION
# =====================================================
import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, timedelta

st.set_page_config(layout="wide")
st.title("🚀 Swing Scanner — Ranking & Accélération")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]

LOOKBACK = 160
TOP_N = 30
DELTA_MIN = 5

W1, W2, W3, W4 = 0.30, 0.25, 0.25, 0.20

@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    return df.iloc[:,0].dropna().astype(str).str.upper().unique().tolist()

TICKERS = load_tickers()

def get_ohlc(t):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)
    url = f"https://api.polygon.io/v2/aggs/ticker/{t}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        if "results" not in d:
            return None
        df = pd.DataFrame(d["results"])
        df["Close"] = df["c"]
        return df
    except:
        return None

def EMA(s,n): return s.ewm(span=n, adjust=False).mean()
def ROC(s,n): return s.pct_change(n)*100

def compute_score(df):
    c = df["Close"]
    s1 = c.iloc[-1] > EMA(c,50).iloc[-1]
    s2 = EMA(c,20).iloc[-1] > EMA(c,20).iloc[-5]
    s3 = ROC(c,5).iloc[-1] > 0
    s4 = ROC(ROC(c,5),5).iloc[-1] > 0
    return round((s1+s2+s3+s4)/4*100,2)

def scan(tickers):
    rows = []
    progress = st.progress(0)
    status = st.empty()

    for i,t in enumerate(tickers):
        status.write(f"{i+1}/{len(tickers)} — {t}")
        df = get_ohlc(t)
        time.sleep(0.25)

        if df is None or len(df)<100:
            continue

        s_today = compute_score(df)
        s_week = compute_score(df.iloc[:-5])
        rows.append([t, round(df["Close"].iloc[-1],2), s_today, s_today-s_week])

        progress.progress((i+1)/len(tickers))

    df_all = pd.DataFrame(rows, columns=["Ticker","Price","Score","Delta"])
    return (
        df_all.sort_values("Score",ascending=False).head(TOP_N),
        df_all[df_all["Delta"]>DELTA_MIN].sort_values("Delta",ascending=False)
    )

limit = st.slider("Nombre de tickers", 50, len(TICKERS), 200)

if st.button("🚀 Scanner Swing"):
    top, accel = scan(TICKERS[:limit])
    st.subheader("🏆 TOP RANKING")
    st.dataframe(top, use_container_width=True)
    st.subheader("⚡ ACCÉLÉRATEURS")
    st.dataframe(accel, use_container_width=True)
