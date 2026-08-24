"""TEA Pullback - scanner S&P 500 avec Yahoo Finance.

Le fichier ``russell3000_constituents.xlsx`` doit etre place dans le meme dossier.
Il doit contenir une colonne ``Symbol`` (ou une colonne dont le nom contient
"sym"). Une colonne ``Sector`` est utilisee lorsqu'elle est disponible.

Le scanner recherche un repli controle dans une tendance haussiere, suivi
d'un debut de reprise. Il retourne les 10 meilleurs candidats et les envoie
sur Discord.

Variable d'environnement requise :
    DISCORD_WEBHOOK_URL
"""

from __future__ import annotations

import calendar
import os
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =====================================================
# CONFIGURATION
# =====================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

CONSTITUENTS_FILE = Path("russell3000_constituents.xlsx")
OUTPUT_CSV = Path("tea_pullback_resultats.csv")

LOOKBACK_DAYS = 550
PULLBACK_LOOKBACK_BARS = 20
STOP_LOOKBACK_BARS = 10

MAX_EMA20_DISTANCE = 3.0
MIN_PULLBACK_DEPTH = 2.0
MAX_PULLBACK_DEPTH = 12.0

RSI_MIN = 40.0
RSI_MAX = 60.0
MIN_RISK_REWARD = 1.50
MIN_SCORE = 55.0

TOP_N = 10
REQUEST_SLEEP = 0.35
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
DISCORD_MAX_LENGTH = 1900


# =====================================================
# VALIDATION ET SESSION HTTP
# =====================================================

if not DISCORD_WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK_URL manquant")


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept-Encoding": "gzip",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


SESSION = build_session()


# =====================================================
# UNIVERS S&P 500
# =====================================================

def load_universe() -> pd.DataFrame:
    if not CONSTITUENTS_FILE.exists():
        raise FileNotFoundError(f"Fichier introuvable : {CONSTITUENTS_FILE}")

    df = pd.read_excel(CONSTITUENTS_FILE)

    if "Symbol" not in df.columns:
        candidates = [column for column in df.columns if "sym" in str(column).lower()]
        if not candidates:
            raise ValueError(
                "Aucune colonne Symbol detectee dans sp500_constituents.xlsx"
            )
        df = df.rename(columns={candidates[0]: "Symbol"})

    if "Sector" not in df.columns:
        sector_candidates = [
            column for column in df.columns
            if "sector" in str(column).lower() or "secteur" in str(column).lower()
        ]
        if sector_candidates:
            df = df.rename(columns={sector_candidates[0]: "Sector"})
        else:
            df["Sector"] = "N/A"

    df["Symbol"] = (
        df["Symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace("/", ".", regex=False)
    )
    df["Sector"] = df["Sector"].fillna("N/A").astype(str).str.strip()

    df = df[
        df["Symbol"].notna()
        & df["Symbol"].ne("")
        & df["Symbol"].ne("NAN")
        & df["Symbol"].ne("SYMBOL")
    ]
    df = df.drop_duplicates(subset=["Symbol"], keep="first")

    print(f"{len(df)} symboles charges depuis {CONSTITUENTS_FILE}")
    return df[["Symbol", "Sector"]].reset_index(drop=True)


# =====================================================
# DONNEES YAHOO FINANCE
# =====================================================

def to_yahoo_symbol(ticker: str) -> str:
    return ticker.replace("/", "-").replace(".", "-")


def get_daily_data(ticker: str, start: date, end: date) -> pd.DataFrame | None:
    yahoo_symbol = to_yahoo_symbol(ticker)
    encoded_symbol = quote(yahoo_symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"

    params = {
        "period1": calendar.timegm(start.timetuple()),
        "period2": calendar.timegm((end + timedelta(days=1)).timetuple()),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )

        if response.status_code != 200:
            print(f"{ticker}: Yahoo Finance HTTP {response.status_code}")
            return None

        payload = response.json()
        chart = payload.get("chart", {})

        if chart.get("error"):
            print(f"{ticker}: Yahoo Finance - {chart['error']}")
            return None

        results = chart.get("result") or []
        if not results:
            return None

        result = results[0]
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        quote_blocks = indicators.get("quote") or []
        adjusted_blocks = indicators.get("adjclose") or []

        if not timestamps or not quote_blocks:
            return None

        market_data = quote_blocks[0]
        raw_close = market_data.get("close") or []
        adjusted_close = (
            adjusted_blocks[0].get("adjclose", [])
            if adjusted_blocks else []
        )

        required = {
            "Open": market_data.get("open") or [],
            "High": market_data.get("high") or [],
            "Low": market_data.get("low") or [],
            "RawClose": raw_close,
            "Volume": market_data.get("volume") or [],
        }

        expected_length = len(timestamps)
        if any(len(values) != expected_length for values in required.values()):
            return None

        if len(adjusted_close) != expected_length:
            adjusted_close = raw_close

        dates = pd.to_datetime(
            timestamps,
            unit="s",
            utc=True,
        ).tz_convert(None).normalize()

        df = pd.DataFrame(required, index=dates)
        df["AdjustedClose"] = adjusted_close

        numeric_columns = [
            "Open",
            "High",
            "Low",
            "RawClose",
            "AdjustedClose",
            "Volume",
        ]
        for column in numeric_columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=["Open", "High", "Low", "RawClose", "AdjustedClose"])
        df = df[df["RawClose"] > 0]

        if df.empty:
            return None

        adjustment_factor = df["AdjustedClose"] / df["RawClose"]
        df["Open"] = df["Open"] * adjustment_factor
        df["High"] = df["High"] * adjustment_factor
        df["Low"] = df["Low"] * adjustment_factor
        df["Close"] = df["AdjustedClose"]

        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()

        # La bougie du jour peut etre encore en formation.
        if not df.empty and df.index[-1].date() >= date.today():
            df = df.iloc[:-1]

        return df if not df.empty else None

    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print(f"{ticker}: erreur Yahoo Finance - {exc}")
        return None


# =====================================================
# INDICATEURS
# =====================================================

def rsi_wilder(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()
    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + relative_strength))

    # Sans perte moyenne, le RSI vaut 100. Si gains et pertes sont tous deux
    # nuls, la valeur neutre de 50 est plus logique.
    result = result.where(average_loss != 0, 100.0)
    flat_market = average_gain.eq(0) & average_loss.eq(0)
    return result.where(~flat_market, 50.0)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    result["EMA20"] = result["Close"].ewm(span=20, adjust=False).mean()
    result["EMA50"] = result["Close"].ewm(span=50, adjust=False).mean()
    result["EMA200"] = result["Close"].ewm(span=200, adjust=False).mean()
    result["RSI14"] = rsi_wilder(result["Close"], 14)

    ema12 = result["Close"].ewm(span=12, adjust=False).mean()
    ema26 = result["Close"].ewm(span=26, adjust=False).mean()
    result["MACD"] = ema12 - ema26
    result["MACDSignal"] = result["MACD"].ewm(span=9, adjust=False).mean()
    result["MACDHistogram"] = result["MACD"] - result["MACDSignal"]

    return result


# =====================================================
# PLAN DE TRADE ET SCORE
# =====================================================

def build_trade_plan(
    close: float,
    prior_high: float,
    recent_low: float,
) -> dict | None:
    stop = recent_low * 0.99
    target = prior_high
    risk = close - stop
    reward = target - close

    if risk <= 0 or reward <= 0:
        return None

    return {
        "stop": stop,
        "target": target,
        "risk_reward": reward / risk,
    }


def compute_score(
    ema20_distance: float,
    rsi_value: float,
    trend_strength: float,
    pullback_depth: float,
    macd_histogram_pct: float,
    macd_rising: bool,
    three_day_return: float,
    risk_reward: float,
) -> float:
    score = 0.0

    # Proximite de l'EMA20 : 25 points.
    score += max(
        0.0,
        25.0 * (1 - ema20_distance / MAX_EMA20_DISTANCE),
    )

    # RSI ideal autour de 50 : 15 points.
    score += max(0.0, 15.0 * (1 - abs(rsi_value - 50.0) / 10.0))

    # Separation EMA50 / EMA200 : 15 points.
    score += min(15.0, max(0.0, trend_strength) * 1.5)

    # Un repli d'environ 5 % est favorise : 15 points.
    pullback_score = 15.0 * (1 - abs(pullback_depth - 5.0) / 7.0)
    score += max(0.0, pullback_score)

    # Reprise du momentum : 15 points.
    score += min(10.0, max(0.0, macd_histogram_pct) * 20.0)
    if macd_rising:
        score += 3.0
    if three_day_return > 0:
        score += 2.0

    # Ratio rendement/risque : 15 points.
    score += min(15.0, max(0.0, risk_reward) * 5.0)

    return round(min(100.0, score), 1)


# =====================================================
# ANALYSE D'UN TITRE
# =====================================================

def analyze_stock(ticker: str, sector: str, df: pd.DataFrame) -> dict | None:
    if len(df) < 220:
        return None

    df = add_indicators(df)
    last = df.iloc[-1]
    previous = df.iloc[-2]

    close = float(last["Close"])
    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    ema200 = float(last["EMA200"])
    rsi_value = float(last["RSI14"])
    macd_histogram = float(last["MACDHistogram"])
    previous_macd_histogram = float(previous["MACDHistogram"])

    values = [close, ema20, ema50, ema200, rsi_value, macd_histogram]
    if any(pd.isna(value) for value in values):
        return None

    # Tendance haussiere propre et prix toujours au-dessus de l'EMA50.
    if not (close > ema50 and ema20 > ema50 > ema200):
        return None

    ema20_distance = abs(close - ema20) / ema20 * 100
    if ema20_distance > MAX_EMA20_DISTANCE:
        return None

    if not (RSI_MIN <= rsi_value <= RSI_MAX):
        return None

    # Le MACD doit deja avoir recommence a confirmer le momentum.
    if macd_histogram <= 0:
        return None

    # Rebond quotidien obligatoire.
    if close <= float(previous["Close"]):
        return None

    # Anti-chute : le titre ne doit pas etre plus bas qu'il y a 10 seances.
    if close < float(df["Close"].iloc[-11]):
        return None

    # Le sommet de reference exclut la bougie actuelle. Cela empeche de
    # qualifier comme pullback un titre qui inscrit simplement un nouveau haut.
    prior_high = float(
        df["High"]
        .shift(1)
        .rolling(PULLBACK_LOOKBACK_BARS)
        .max()
        .iloc[-1]
    )
    if pd.isna(prior_high) or prior_high <= 0:
        return None

    pullback_depth = (prior_high - close) / prior_high * 100
    if not (MIN_PULLBACK_DEPTH <= pullback_depth <= MAX_PULLBACK_DEPTH):
        return None

    recent_low = float(df["Low"].tail(STOP_LOOKBACK_BARS).min())
    plan = build_trade_plan(close, prior_high, recent_low)

    if plan is None or plan["risk_reward"] < MIN_RISK_REWARD:
        return None

    trend_strength = (ema50 - ema200) / ema200 * 100
    macd_histogram_pct = macd_histogram / close * 100
    macd_rising = macd_histogram > previous_macd_histogram
    three_day_return = (close / float(df["Close"].iloc[-4]) - 1) * 100

    score = compute_score(
        ema20_distance=ema20_distance,
        rsi_value=rsi_value,
        trend_strength=trend_strength,
        pullback_depth=pullback_depth,
        macd_histogram_pct=macd_histogram_pct,
        macd_rising=macd_rising,
        three_day_return=three_day_return,
        risk_reward=plan["risk_reward"],
    )

    if score < MIN_SCORE:
        return None

    reasons = [
        "EMA20 > EMA50 > EMA200",
        f"Repli {pullback_depth:.2f}%",
        f"RSI {rsi_value:.1f}",
        "MACD positif",
        "Rebond quotidien",
        f"R/R {plan['risk_reward']:.2f}",
    ]

    if macd_rising:
        reasons.append("Histogramme MACD en hausse")
    if close > float(last["Open"]):
        reasons.append("Bougie haussiere")

    return {
        "Ticker": ticker,
        "Sector": sector,
        "Price": round(close, 2),
        "Score": score,
        "EMA20 distance %": round(ema20_distance, 2),
        "Pullback depth %": round(pullback_depth, 2),
        "RSI14": round(rsi_value, 1),
        "MACD histogram %": round(macd_histogram_pct, 4),
        "3-day return %": round(three_day_return, 2),
        "Stop": round(plan["stop"], 2),
        "Target": round(plan["target"], 2),
        "Risk/Reward": round(plan["risk_reward"], 2),
        "Reasons": " | ".join(reasons),
    }


# =====================================================
# DISCORD
# =====================================================

def split_message(message: str, limit: int = DISCORD_MAX_LENGTH) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""

    for block in message.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"

        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(block) <= limit:
            current = block
        else:
            chunks.append(block[:limit])
            current = block[limit:]

    if current:
        chunks.append(current)

    return chunks


def send_discord(message: str) -> None:
    for chunk in split_message(message):
        try:
            response = SESSION.post(
                DISCORD_WEBHOOK_URL,
                json={"content": chunk},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )

            if response.status_code not in (200, 204):
                print(
                    f"Discord HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            else:
                print("Message Discord envoye")

        except requests.RequestException as exc:
            print(f"Erreur Discord : {exc}")


def build_report(results: list[dict], analysed: int) -> str:
    if not results:
        return (
            "↩️ **TEA PULLBACK - S&P 500**\n\n"
            f"{analysed} titres analyses.\n"
            "Aucun pullback ne respecte toutes les confirmations aujourd'hui."
        )

    sections = [
        "↩️ **TEA PULLBACK - S&P 500**\n"
        f"{analysed} titres analyses | {len(results)} candidat(s) retenu(s)"
    ]
    medals = ["🥇", "🥈", "🥉"]

    for index, result in enumerate(results):
        marker = medals[index] if index < 3 else "📈"
        sections.append(
            f"{marker} **{result['Ticker']}** | {result['Sector']} | "
            f"Score {result['Score']}/100\n"
            f"Prix ${result['Price']} | Repli {result['Pullback depth %']}% | "
            f"RSI {result['RSI14']}\n"
            f"Stop ${result['Stop']} | Cible ${result['Target']} | "
            f"R/R {result['Risk/Reward']}"
        )

    sections.append(
        "Plan technique indicatif : confirmer le comportement du prix avant toute entree."
    )
    return "\n\n".join(sections)


# =====================================================
# PROGRAMME PRINCIPAL
# =====================================================

def main() -> None:
    print("Demarrage de TEA Pullback - Yahoo Finance")

    universe = load_universe()
    total = len(universe)

    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    candidates: list[dict] = []
    valid_histories = 0

    for index, row in universe.iterrows():
        ticker = row["Symbol"]
        sector = row["Sector"]
        position = index + 1

        print(f"{position}/{total} - {ticker}")

        df = get_daily_data(ticker, start, end)
        time.sleep(REQUEST_SLEEP)

        if df is None:
            continue

        valid_histories += 1
        result = analyze_stock(ticker, sector, df)

        if result is not None:
            candidates.append(result)

    candidates = sorted(
        candidates,
        key=lambda item: (
            item["Score"],
            item["Risk/Reward"],
            -item["EMA20 distance %"],
        ),
        reverse=True,
    )[:TOP_N]

    if candidates:
        pd.DataFrame(candidates).to_csv(
            OUTPUT_CSV,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"Resultats enregistres dans {OUTPUT_CSV}")

    report = build_report(candidates, valid_histories)
    print("\n" + report + "\n")
    send_discord(report)

    print(
        f"Scan termine | {valid_histories}/{total} historiques valides | "
        f"{len(candidates)} candidat(s)"
    )


if __name__ == "__main__":
    main()
