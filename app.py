
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "paper_trades.sqlite3"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

st.set_page_config(page_title="BTC 15m Paper Trader", page_icon="₿", layout="wide")

# ---------- persistence ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            title TEXT,
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            estimated_fee REAL NOT NULL,
            total_cost REAL NOT NULL,
            opened_at TEXT NOT NULL,
            close_time TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            result TEXT,
            pnl REAL
        )
    """)
    conn.commit()
    return conn

def load_trades():
    with db() as conn:
        return pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)

def add_trade(market, side, contracts, price, fee, total_cost):
    with db() as conn:
        conn.execute("""
            INSERT INTO trades
            (ticker,title,side,contracts,entry_price,estimated_fee,total_cost,opened_at,close_time,status)
            VALUES (?,?,?,?,?,?,?,?,?,'OPEN')
        """, (
            market.get("ticker"), market.get("title"), side, int(contracts),
            float(price), float(fee), float(total_cost),
            datetime.now(timezone.utc).isoformat(), market.get("close_time")
        ))
        conn.commit()

def settle_open_trades():
    trades = load_trades()
    open_trades = trades[trades["status"] == "OPEN"] if not trades.empty else trades
    for _, trade in open_trades.iterrows():
        try:
            market = get_market(trade["ticker"])
            status = str(market.get("status", "")).lower()
            result = str(market.get("result", "")).lower()
            if status in ("settled", "finalized") and result in ("yes", "no"):
                won = (trade["side"].lower() == result)
                gross = (1.0 if won else 0.0) * int(trade["contracts"]) - float(trade["entry_price"]) * int(trade["contracts"])
                pnl = round(gross - float(trade["estimated_fee"]), 4)
                with db() as conn:
                    conn.execute(
                        "UPDATE trades SET status='SETTLED', result=?, pnl=? WHERE id=?",
                        (result.upper(), pnl, int(trade["id"]))
                    )
                    conn.commit()
        except Exception:
            # A temporary API failure should not alter a paper position.
            continue

# ---------- market data ----------
@st.cache_data(ttl=4, show_spinner=False)
def get_open_markets():
    r = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": "KXBTC15M", "status": "open", "limit": 100},
        timeout=12,
        headers={"User-Agent": "BTC15mPaperTrader/1.0"}
    )
    r.raise_for_status()
    payload = r.json()
    markets = payload.get("markets", [])
    return markets

@st.cache_data(ttl=3, show_spinner=False)
def get_market(ticker):
    r = requests.get(f"{KALSHI_BASE}/markets/{ticker}", timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("market", data)

@st.cache_data(ttl=3, show_spinner=False)
def get_orderbook(ticker):
    r = requests.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook", timeout=10)
    r.raise_for_status()
    return r.json().get("orderbook_fp", {})

@st.cache_data(ttl=8, show_spinner=False)
def get_btc_candles():
    # Coinbase spot is only a proxy; Kalshi's 15m BTC contracts settle on CF Benchmarks BRTI.
    now = int(datetime.now(timezone.utc).timestamp())
    r = requests.get(
        COINBASE_CANDLES,
        params={"granularity": 60, "start": now - 60 * 80, "end": now},
        timeout=10,
        headers={"User-Agent": "BTC15mPaperTrader/1.0"}
    )
    r.raise_for_status()
    # Coinbase format: [time, low, high, open, close, volume], newest first.
    raw = r.json()
    df = pd.DataFrame(raw, columns=["time", "low", "high", "open", "close", "volume"])
    df = df.sort_values("time").reset_index(drop=True)
    for c in ["low", "high", "open", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna()

def dollars(market, key_dollars, key_cents):
    val = market.get(key_dollars)
    if val not in (None, ""):
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    val = market.get(key_cents)
    if val not in (None, ""):
        try:
            return float(val) / 100.0
        except (TypeError, ValueError):
            pass
    return None

def best_bid(levels):
    vals = []
    for row in levels or []:
        try:
            vals.append((float(row[0]), float(row[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return max(vals, key=lambda x: x[0]) if vals else (None, 0.0)

def quote_for(market):
    book = get_orderbook(market["ticker"])
    yes_levels = book.get("yes_dollars", [])
    no_levels = book.get("no_dollars", [])
    yes_bid, yes_bid_qty = best_bid(yes_levels)
    no_bid, no_bid_qty = best_bid(no_levels)
    if yes_bid is None:
        yes_bid = dollars(market, "yes_bid_dollars", "yes_bid")
    if no_bid is None:
        no_bid = dollars(market, "no_bid_dollars", "no_bid")
    yes_ask = (1.0 - no_bid) if no_bid is not None else dollars(market, "yes_ask_dollars", "yes_ask")
    no_ask = (1.0 - yes_bid) if yes_bid is not None else dollars(market, "no_ask_dollars", "no_ask")
    return {
        "yes_bid": yes_bid, "yes_ask": yes_ask,
        "no_bid": no_bid, "no_ask": no_ask,
        "yes_bid_qty": yes_bid_qty, "no_bid_qty": no_bid_qty,
    }

def calc_indicators(df):
    if len(df) < 20:
        raise ValueError("Not enough 1-minute BTC candles yet.")
    close = df["close"]
    ret1 = (close.iloc[-1] / close.iloc[-2] - 1) * 100
    ret5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
    ret15 = (close.iloc[-1] / close.iloc[-16] - 1) * 100
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] and not np.isnan(loss.iloc[-1]) else np.inf
    rsi = 100 - (100 / (1 + rs)) if np.isfinite(rs) else 100.0
    vol_avg = df["volume"].iloc[-21:-1].mean()
    vol_ratio = df["volume"].iloc[-1] / vol_avg if vol_avg and vol_avg > 0 else 1.0
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    return {
        "last": float(close.iloc[-1]), "ret1": float(ret1), "ret5": float(ret5),
        "ret15": float(ret15), "rsi": float(rsi), "vol_ratio": float(vol_ratio),
        "ema9": float(ema9), "ema21": float(ema21),
    }

def signal_score(ind):
    # Transparent, deliberately conservative heuristic. Not a trained/calibrated probability model.
    score = 0.0
    score += np.clip(ind["ret1"] / 0.035, -1, 1) * 0.20
    score += np.clip(ind["ret5"] / 0.10, -1, 1) * 0.35
    score += np.clip(ind["ret15"] / 0.20, -1, 1) * 0.20
    score += (0.15 if ind["ema9"] > ind["ema21"] else -0.15)
    if ind["rsi"] > 70:
        score -= 0.10
    elif ind["rsi"] < 30:
        score += 0.10
    if ind["vol_ratio"] > 3.0:
        score *= 0.65
    return float(np.clip(score, -1, 1))

def estimate_probability(score):
    # A score-to-probability display is intentionally conservative and UNCALIBRATED.
    # It is only a ranking aid, not a statistically validated win probability.
    return float(0.50 + 0.22 * score)

def fee_estimate(price, contracts, maker=False):
    # Kalshi published general schedule: 7% taker / 1.75% maker of P*(1-P)*contracts.
    # Actual market-specific fees and rounding must be verified on Kalshi before trading.
    rate = 0.0175 if maker else 0.07
    raw = rate * int(contracts) * float(price) * (1 - float(price))
    return math.ceil(raw * 100 - 1e-10) / 100

def market_times(m):
    close = m.get("close_time") or m.get("expected_expiration_time") or ""
    open_ = m.get("open_time") or ""
    return open_, close

def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

# ---------- UI ----------
st.title("₿ Kalshi BTC 15-minute — Paper Trading Desk")
st.caption("Local research tool • automatic scanning • manual approval • simulated orders only")

with st.sidebar:
    st.header("Controls")
    bankroll = st.number_input("Paper bankroll ($)", min_value=10.0, max_value=99.0, value=75.0, step=5.0)
    max_risk_pct = st.slider("Max bankroll at risk per paper trade", 0.5, 3.0, 1.5, 0.25)
    min_edge = st.slider("Minimum model-vs-market gap (percentage points)", 1, 15, 7, 1)
    refresh_seconds = st.select_slider("Refresh interval", options=[5, 10, 15, 30], value=10)
    st.info("No Kalshi credentials are needed for public market data. This build cannot place live orders.")
    st_autorefresh(interval=refresh_seconds * 1000, key="auto_refresh")

if st.button("Refresh data now", type="primary"):
    st.cache_data.clear()
    st.rerun()

try:
    markets = get_open_markets()
    candles = get_btc_candles()
    indicators = calc_indicators(candles)
except Exception as e:
    st.error(f"Could not load live data: {e}")
    st.markdown("Check your internet connection and try again. The app uses Kalshi's public market-data API and Coinbase 1-minute candles.")
    st.stop()

if not markets:
    st.warning("No open KXBTC15M market was returned by Kalshi right now. Refresh in a few seconds.")
    st.stop()

score = signal_score(indicators)
p_up = estimate_probability(score)
direction = "UP / YES" if score > 0.08 else ("DOWN / NO" if score < -0.08 else "NO CLEAR DIRECTION")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("BTC spot proxy", f"${indicators['last']:,.2f}")
c2.metric("1m change", f"{indicators['ret1']:+.3f}%")
c3.metric("5m change", f"{indicators['ret5']:+.3f}%")
c4.metric("15m change", f"{indicators['ret15']:+.3f}%")
c5.metric("RSI (14)", f"{indicators['rsi']:.1f}")

st.subheader("Market scanner")
rows = []
quotes = {}
for m in markets:
    try:
        q = quote_for(m)
        quotes[m["ticker"]] = q
        close_dt = parse_time(m.get("close_time") or m.get("expected_expiration_time"))
        secs_left = (close_dt - datetime.now(timezone.utc)).total_seconds() if close_dt else None
        rows.append({
            "Ticker": m.get("ticker"),
            "Market": m.get("title"),
            "YES bid": q["yes_bid"],
            "YES ask": q["yes_ask"],
            "NO bid": q["no_bid"],
            "NO ask": q["no_ask"],
            "Seconds left": round(secs_left) if secs_left is not None else None,
            "Volume": m.get("volume_fp", m.get("volume")),
            "Close": m.get("close_time", ""),
        })
    except Exception:
        continue

if not rows:
    st.warning("Markets were found, but order-book quotes could not be loaded. Try refreshing.")
    st.stop()

table = pd.DataFrame(rows)
table["YES ask"] = pd.to_numeric(table["YES ask"], errors="coerce")
table["NO ask"] = pd.to_numeric(table["NO ask"], errors="coerce")
st.dataframe(table, use_container_width=True, hide_index=True)

# Pick the nearest-closing market with a usable quote and enough time remaining.
usable = [m for m in markets if m["ticker"] in quotes]
usable.sort(key=lambda m: parse_time(m.get("close_time") or m.get("expected_expiration_time")) or datetime.max.replace(tzinfo=timezone.utc))
selected = usable[0] if usable else markets[0]
selected_quote = quotes.get(selected["ticker"], {})
close_dt = parse_time(selected.get("close_time") or selected.get("expected_expiration_time"))
seconds_left = (close_dt - datetime.now(timezone.utc)).total_seconds() if close_dt else None

st.subheader("Signal & approval")
left, right = st.columns([1.15, 1])
with left:
    st.metric("Current directional read", direction)
    st.caption("The score-to-probability conversion is uncalibrated. Treat it as a research score, not a proven probability.")
    st.progress((score + 1) / 2)
    st.write({
        "Experimental UP score": round(score, 3),
        "Experimental UP probability display": f"{p_up*100:.1f}%",
        "EMA 9 / EMA 21": f"${indicators['ema9']:,.2f} / ${indicators['ema21']:,.2f}",
        "Volume vs prior 20m avg": f"{indicators['vol_ratio']:.2f}x",
    })
with right:
    st.write(f"**Selected market:** {selected.get('title', selected['ticker'])}")
    st.code(selected["ticker"])
    st.write(f"**Time remaining:** {seconds_left:.0f} seconds" if seconds_left is not None else "**Time remaining:** unavailable")
    st.write(f"**YES ask:** {selected_quote.get('yes_ask')}")
    st.write(f"**NO ask:** {selected_quote.get('no_ask')}")

# For a long YES position, market asks are complements of the best NO bid; same for NO.
candidate_side = "YES" if score > 0.08 else ("NO" if score < -0.08 else "YES")
candidate_price = selected_quote.get("yes_ask" if candidate_side == "YES" else "no_ask")
candidate_model_prob = p_up if candidate_side == "YES" else (1 - p_up)
market_implied = candidate_price if candidate_price is not None else None
edge_pp = (candidate_model_prob - market_implied) * 100 if market_implied is not None else None
risk_cap = bankroll * max_risk_pct / 100
contracts_max = int(risk_cap // candidate_price) if candidate_price and candidate_price > 0 else 0
estimated_fee_one = fee_estimate(candidate_price, 1) if candidate_price is not None and 0 < candidate_price < 1 else 0
one_contract_total = (candidate_price + estimated_fee_one) if candidate_price is not None else None

# Conservative gates: time window, quote validity, edge threshold, and max-risk budget.
reasons = []
if abs(score) < 0.18:
    reasons.append("Directional signal is too weak.")
if candidate_price is None or not (0 < candidate_price < 1):
    reasons.append("No valid executable ask is available.")
if seconds_left is None or seconds_left < 180 or seconds_left > 720:
    reasons.append("Outside the initial 3–12 minute entry window.")
if edge_pp is None or edge_pp < min_edge:
    reasons.append(f"Estimated gap is below your {min_edge} pp threshold.")
if contracts_max < 1:
    reasons.append("One contract exceeds the per-trade risk budget.")
if candidate_price is not None and candidate_price > 0.92:
    reasons.append("Contract price is too close to $1; reward is too small.")

st.write(f"**Candidate:** {candidate_side} at approximately ${candidate_price:.2f}" if candidate_price is not None else "**Candidate:** no valid quote")
st.write(f"**Model-vs-market gap:** {edge_pp:+.1f} percentage points" if edge_pp is not None else "**Model-vs-market gap:** unavailable")
if reasons:
    st.warning("NO TRADE — " + " ".join(reasons))
else:
    st.success("PAPER TRADE CANDIDATE — review the quote, market rules, and risk before approving.")

with st.form("approve_paper_trade"):
    side_choice = st.selectbox("Signal side to paper trade", [candidate_side])
    chosen_price = selected_quote.get("yes_ask" if side_choice == "YES" else "no_ask")
    max_contracts = int(risk_cap // chosen_price) if chosen_price and chosen_price > 0 else 0
    contracts = st.number_input("Contracts", min_value=1, max_value=max(1, min(max_contracts, 20)), value=1, step=1)
    submit = st.form_submit_button("Approve PAPER trade", disabled=bool(reasons) or max_contracts < 1)
    if submit:
        fee = fee_estimate(chosen_price, int(contracts))
        cost = chosen_price * int(contracts) + fee
        if cost > risk_cap + 1e-9:
            st.error(f"Trade blocked: ${cost:.2f} exceeds the ${risk_cap:.2f} risk cap.")
        else:
            add_trade(selected, side_choice, int(contracts), chosen_price, fee, cost)
            st.success(f"Paper trade recorded: {contracts} {side_choice} contract(s), estimated total cost ${cost:.2f}.")
            st.rerun()

st.caption("Important: Coinbase BTC spot is a proxy, not the CF Benchmarks BRTI settlement index. Kalshi's 15-minute contract compares the 60-second average before close with the 60-second average before open. This dashboard does not yet ingest the official BRTI feed or historical Kalshi data, so its score is exploratory and not a validated edge.")

# ---------- paper portfolio ----------
settle_open_trades()
trades = load_trades()
st.subheader("Paper portfolio")
if trades.empty:
    st.info("No paper trades yet. The scanner will identify candidates; you decide whether to approve them.")
else:
    settled = trades[trades["status"] == "SETTLED"]
    open_ = trades[trades["status"] == "OPEN"]
    wins = int((settled["pnl"] > 0).sum()) if not settled.empty else 0
    losses = int((settled["pnl"] <= 0).sum()) if not settled.empty else 0
    pnl_total = float(settled["pnl"].sum()) if not settled.empty else 0.0
    a, b, c, d = st.columns(4)
    a.metric("Settled trades", len(settled))
    b.metric("Win rate", f"{wins / len(settled) * 100:.1f}%" if len(settled) else "—")
    c.metric("Realized P/L", f"${pnl_total:+.2f}")
    d.metric("Open paper positions", len(open_))
    st.dataframe(trades, use_container_width=True, hide_index=True)
    csv = trades.to_csv(index=False).encode("utf-8")
    st.download_button("Download trade history CSV", csv, "kalshi_btc_paper_trades.csv", "text/csv")

st.divider()
st.caption("Research only. No live orders, deposits, withdrawals, or account access. Paper fills assume the displayed ask and do not model queue position, partial fills, or all market-specific fees.")
