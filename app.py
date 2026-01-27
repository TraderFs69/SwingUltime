import streamlit as st
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
