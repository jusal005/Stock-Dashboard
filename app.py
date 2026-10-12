"""
Real-Time Stock Market Dashboard  (v2)
Stack : Python, Requests, Pandas, Plotly, Streamlit
Data  : Yahoo Finance public chart endpoint (no API key) + built-in Demo mode
Run   : streamlit run app.py
"""
import html
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Stock Dashboard", page_icon="📈", layout="wide")

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}
PERIODS = {  # label -> (range, interval)
    "1D": ("1d", "1m"), "5D": ("5d", "5m"), "1M": ("1mo", "30m"), "3M": ("3mo", "1h"),
    "6M": ("6mo", "1d"), "1Y": ("1y", "1d"), "5Y": ("5y", "1wk"),
}
DEMO_SHAPE = {  # label -> (points, pandas freq)
    "1D": (240, "1min"), "5D": (300, "5min"), "1M": (160, "30min"), "3M": (200, "1h"),
    "6M": (126, "B"), "1Y": (252, "B"), "5Y": (260, "W-FRI"),
}
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "AMZN", "RELIANCE.NS", "TCS.NS"]
UP, DOWN, ACCENT = "#22c58b", "#f0616d", "#6c8cff"
FRESH_SECS = 30        # reuse data younger than this (protects against rate limits)
COOLDOWN_SECS = 90     # pause requests after an HTTP 429


# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
.stApp {{ background: radial-gradient(1200px 600px at 10% -10%, #1b2250 0%, rgba(13,17,30,0) 60%),
                      radial-gradient(900px 500px at 100% 0%, #123a3a 0%, rgba(13,17,30,0) 55%), #0b0f1a; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2rem; max-width: 1500px; }}
section[data-testid="stSidebar"] {{ background: #0f1424; border-right: 1px solid rgba(255,255,255,.06); }}

.hero h1 {{ font-size: 2.3rem; font-weight: 800; margin: 0; letter-spacing: -.5px;
           background: linear-gradient(90deg, #fff, {ACCENT} 60%, {UP}); -webkit-background-clip: text;
           -webkit-text-fill-color: transparent; }}
.hero p {{ color: #8b93a7; margin: .2rem 0 1rem 0; }}
.pill {{ display:inline-block; padding: 3px 10px; border-radius: 999px; font-size: .75rem; font-weight: 600; }}
.pill.up {{ color:{UP}; background: rgba(34,197,139,.14); }}
.pill.down {{ color:{DOWN}; background: rgba(240,97,109,.14); }}
.pill.live {{ color:{UP}; background: rgba(34,197,139,.14); }}
.pill.stale {{ color:#f5b942; background: rgba(245,185,66,.14); }}
.pill.demo {{ color:{ACCENT}; background: rgba(108,140,255,.16); }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:currentColor; margin-right:6px;
       animation: pulse 1.6s infinite; }}
@keyframes pulse {{ 0%{{opacity:1}} 50%{{opacity:.25}} 100%{{opacity:1}} }}

.card {{ background: linear-gradient(160deg, rgba(255,255,255,.06), rgba(255,255,255,.02));
        border: 1px solid rgba(255,255,255,.08); border-radius: 18px; padding: 16px 18px; margin-bottom: 14px;
        backdrop-filter: blur(6px); transition: transform .15s, border-color .15s; }}
.card:hover {{ transform: translateY(-2px); border-color: rgba(108,140,255,.45); }}
.card .top {{ display:flex; justify-content:space-between; align-items:center; }}
.card .sym {{ font-weight: 700; font-size: 1.05rem; color:#fff; }}
.card .name {{ color:#8b93a7; font-size:.75rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px; }}
.card .price {{ font-size: 1.7rem; font-weight: 700; color:#fff; margin-top: 8px; letter-spacing:-.5px; }}
.card .cur {{ font-size:.8rem; color:#8b93a7; font-weight:500; margin-left:4px; }}
.card .foot {{ display:flex; justify-content:space-between; color:#8b93a7; font-size:.72rem; margin-top:8px; }}
.kpi .label {{ color:#8b93a7; font-size:.78rem; text-transform:uppercase; letter-spacing:.8px; }}
.kpi .value {{ color:#fff; font-size:1.6rem; font-weight:700; margin-top:4px; }}
.section {{ font-size:1.15rem; font-weight:700; color:#e6e9f2; margin: 18px 0 8px 0; }}
button[data-baseweb="tab"] {{ font-weight:600; }}
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #
class RateLimited(Exception):
    pass


@st.cache_resource
def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:  # warm-up request: gets the cookies Yahoo likes to see
        s.get("https://fc.yahoo.com", timeout=5)
    except requests.RequestException:
        pass
    return s


@st.cache_resource
def get_store() -> dict:
    """Shared across sessions: last good data per (symbol, period) + rate-limit cooldown."""
    return {"data": {}, "cooldown": 0.0}


def parse_chart(payload: dict):
    chart = payload["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError("Symbol not found")
    res = chart["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame(
        {"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"], "Volume": q["volume"]},
        index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
    ).dropna(subset=["Close"])
    meta = res["meta"]
    if meta.get("exchangeTimezoneName"):
        df.index = df.index.tz_convert(meta["exchangeTimezoneName"])
    df.index.name = "Time"
    if df.empty:
        raise ValueError("No data returned")
    return df, meta


def fetch_yahoo(symbol: str, rng: str, interval: str):
    session = get_session()
    params = {"range": rng, "interval": interval, "includePrePost": "false"}
    saw_429 = False
    for attempt in range(2):
        for host in HOSTS:
            try:
                r = session.get(f"https://{host}/v8/finance/chart/{symbol}", params=params, timeout=10)
            except requests.RequestException:
                continue
            if r.status_code == 429:
                saw_429 = True
                continue
            if r.status_code == 404:
                raise ValueError("Symbol not found")
            if r.ok:
                return parse_chart(r.json())
        time.sleep(1.2 * (attempt + 1))
    if saw_429:
        raise RateLimited()
    raise ConnectionError("Could not reach Yahoo Finance")


def demo_history(symbol: str, period: str):
    """Simulated OHLCV so the dashboard always works (offline / rate-limited / presentations)."""
    n, freq = DEMO_SHAPE[period]
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(symbol)) % (2**32)
    rng = np.random.default_rng(seed)
    start = 50 + (seed % 450)
    vol = {"1min": .0006, "5min": .0012, "30min": .003, "1h": .004, "B": .014, "W-FRI": .03}[freq]
    close = start * np.exp(np.cumsum(rng.normal(0.0001, vol, n)))
    # tiny time-based wobble on the latest point so auto-refresh visibly "moves"
    close[-1] *= 1 + np.random.default_rng(int(time.time() // 10) + seed).normal(0, vol / 3)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.integers(2e5, 5e6, n)
    idx = pd.date_range(end=pd.Timestamp.now(), periods=n, freq=freq)
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
    df.index.name = "Time"
    meta = {"currency": "USD", "longName": f"{symbol} · simulated", "chartPreviousClose": float(close[0])}
    return df, meta


def get_data(symbol: str, period: str, demo: bool):
    """Returns (df, meta, status) where status is 'live' | 'stale' | 'demo'."""
    if demo:
        df, meta = demo_history(symbol, period)
        return df, meta, "demo"

    rng, interval = PERIODS[period]
    store, now = get_store(), time.time()
    key = (symbol, period)
    entry = store["data"].get(key)

    if entry and now - entry["ts"] < FRESH_SECS:
        return entry["df"], entry["meta"], "live"

    def fallback(err: Exception):
        if entry:
            return entry["df"], entry["meta"], "stale"
        raise err

    if now < store["cooldown"]:
        return fallback(RateLimited())
    try:
        df, meta = fetch_yahoo(symbol, rng, interval)
    except RateLimited as exc:
        store["cooldown"] = now + COOLDOWN_SECS
        return fallback(exc)
    except Exception as exc:
        return fallback(exc)
    store["data"][key] = {"df": df, "meta": meta, "ts": now}
    return df, meta, "live"


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def bollinger(s, n=20, k=2.0):
    mid, std = s.rolling(n).mean(), s.rolling(n).std()
    return mid + k * std, mid, mid - k * std


def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def macd(s, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    day = df.index.date
    return (tp * df["Volume"]).groupby(day).cumsum() / df["Volume"].groupby(day).cumsum().replace(0, np.nan)


def summarize(df, meta, period):
    last = float(df["Close"].iloc[-1])
    prev = (meta.get("chartPreviousClose") or meta.get("previousClose") or float(df["Close"].iloc[0])) \
        if period == "1D" else float(df["Close"].iloc[0])
    return {
        "price": last, "change": last - prev, "pct": (last - prev) / prev * 100 if prev else 0.0,
        "high": float(df["High"].max()), "low": float(df["Low"].min()),
        "volume": int(df["Volume"].fillna(0).sum()), "currency": meta.get("currency", ""),
        "name": meta.get("longName") or meta.get("shortName") or "",
    }


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def sparkline(values, color, w=300, h=44):
    v = [float(x) for x in values if x == x][-80:]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    span = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 3 - (x - lo) / span * (h - 6):.1f}" for i, x in enumerate(v))
    area = f"0,{h} {pts} {w},{h}"
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" opacity="0.12"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/></svg>')


def human(n: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def stock_card(sym, s, closes) -> str:
    up = s["change"] >= 0
    color, cls, arrow = (UP, "up", "▲") if up else (DOWN, "down", "▼")
    return (
        '<div class="card">'
        f'<div class="top"><div><div class="sym">{html.escape(sym)}</div>'
        f'<div class="name">{html.escape(s["name"])}</div></div>'
        f'<span class="pill {cls}">{arrow} {s["pct"]:+.2f}%</span></div>'
        f'<div class="price">{s["price"]:,.2f}<span class="cur">{html.escape(s["currency"])}</span></div>'
        f'{sparkline(closes, color)}'
        f'<div class="foot"><span>H {s["high"]:,.2f} · L {s["low"]:,.2f}</span><span>Vol {human(s["volume"])}</span></div>'
        '</div>'
    )


def kpi(label, value, color="#fff") -> str:
    return (f'<div class="card kpi"><div class="label">{label}</div>'
            f'<div class="value" style="color:{color}">{value}</div></div>')


def style_fig(fig, height):
    fig.update_layout(
        template="plotly_dark", height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
        font=dict(family="Inter, sans-serif", color="#c9cfdf"),
        legend=dict(orientation="h", y=1.05, x=0, bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,.05)", rangebreaks=[dict(bounds=["sat", "mon"])], rangeslider_visible=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,.05)")
    return fig


def technical_chart(df, symbol, style, overlays, show_rsi, show_macd, show_volume):
    rows = ["price"] + (["volume"] if show_volume else []) + (["rsi"] if show_rsi else []) + (["macd"] if show_macd else [])
    heights = {"price": .55, "volume": .13, "rsi": .16, "macd": .16}
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=.03,
                        row_heights=[heights[r] for r in rows])
    row_of = {n: i + 1 for i, n in enumerate(rows)}
    close = df["Close"]

    if style == "Candlestick":
        fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=close,
                                     name=symbol, increasing_line_color=UP, decreasing_line_color=DOWN,
                                     increasing_fillcolor=UP, decreasing_fillcolor=DOWN), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(x=df.index, y=close, name=symbol, line=dict(color=ACCENT, width=2.2),
                                 fill="tozeroy", fillcolor="rgba(108,140,255,.08)"), row=1, col=1)
        fig.update_yaxes(range=[close.min() * .995, close.max() * 1.005], row=1, col=1)

    line = lambda c, w=1.4, d=None: dict(color=c, width=w, dash=d)
    if "SMA 20" in overlays: fig.add_trace(go.Scatter(x=df.index, y=sma(close, 20), name="SMA 20", line=line("#ffb84d")), row=1, col=1)
    if "SMA 50" in overlays: fig.add_trace(go.Scatter(x=df.index, y=sma(close, 50), name="SMA 50", line=line("#c77dff")), row=1, col=1)
    if "EMA 20" in overlays: fig.add_trace(go.Scatter(x=df.index, y=ema(close, 20), name="EMA 20", line=line("#4dd0e1")), row=1, col=1)
    if "Bollinger Bands" in overlays:
        up, _, lo = bollinger(close)
        fig.add_trace(go.Scatter(x=df.index, y=up, name="BB upper", line=line("rgba(170,178,200,.6)", 1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=lo, name="BB lower", line=line("rgba(170,178,200,.6)", 1),
                                 fill="tonexty", fillcolor="rgba(170,178,200,.07)"), row=1, col=1)
    if "VWAP" in overlays and df["Volume"].fillna(0).sum() > 0:
        fig.add_trace(go.Scatter(x=df.index, y=vwap(df), name="VWAP", line=line("#ffee58", 1.4, "dot")), row=1, col=1)

    if show_volume:
        colors = [UP if c >= o else DOWN for o, c in zip(df["Open"], close)]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=colors, opacity=.7, name="Volume", showlegend=False),
                      row=row_of["volume"], col=1)
    if show_rsi:
        r = row_of["rsi"]
        fig.add_trace(go.Scatter(x=df.index, y=rsi(close), name="RSI 14", line=line("#c77dff", 1.6)), row=r, col=1)
        fig.add_hrect(y0=30, y1=70, fillcolor="rgba(255,255,255,.03)", line_width=0, row=r, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color=DOWN, opacity=.5, row=r, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color=UP, opacity=.5, row=r, col=1)
        fig.update_yaxes(range=[0, 100], row=r, col=1)
    if show_macd:
        r = row_of["macd"]
        ln, sg, hist = macd(close)
        fig.add_trace(go.Bar(x=df.index, y=hist, name="Histogram", showlegend=False,
                             marker_color=[UP if h >= 0 else DOWN for h in hist.fillna(0)]), row=r, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=ln, name="MACD", line=line("#5aa9ff")), row=r, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=sg, name="Signal", line=line("#ff8a65")), row=r, col=1)
    return style_fig(fig, 260 + 180 * len(rows))


def comparison_chart(series: dict):
    palette = [ACCENT, UP, "#ffb84d", "#c77dff", "#4dd0e1", DOWN, "#ffee58", "#90a4ae"]
    fig = go.Figure()
    for i, (sym, close) in enumerate(series.items()):
        norm = (close / close.iloc[0] - 1) * 100
        fig.add_trace(go.Scatter(x=norm.index, y=norm, name=sym, line=dict(color=palette[i % len(palette)], width=2.2)))
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=.5)
    fig.update_yaxes(ticksuffix="%")
    return style_fig(fig, 420)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
st.sidebar.markdown("## 📈 Controls")
tickers = st.sidebar.multiselect("Tickers", DEFAULT_TICKERS, default=["AAPL", "MSFT", "NVDA"])
custom = st.sidebar.text_input("Add custom tickers", placeholder="AMZN, INFY.NS, BTC-USD")
for t in custom.replace(";", ",").split(","):
    t = t.strip().upper()
    if t and t not in tickers:
        tickers.append(t)

period = st.sidebar.select_slider("Period", options=list(PERIODS), value="1D")
chart_style = st.sidebar.radio("Chart type", ["Candlestick", "Line"], horizontal=True)
overlays = st.sidebar.multiselect("Overlays", ["SMA 20", "SMA 50", "EMA 20", "Bollinger Bands", "VWAP"],
                                  default=["SMA 20", "Bollinger Bands"])
c1, c2, c3 = st.sidebar.columns(3)
show_volume, show_rsi, show_macd = c1.checkbox("Vol", True), c2.checkbox("RSI", True), c3.checkbox("MACD", True)

st.sidebar.divider()
demo = st.sidebar.toggle("🧪 Demo mode (simulated data)", value=False,
                         help="Works offline and ignores Yahoo rate limits. Great for presentations.")
auto = st.sidebar.toggle("Auto-refresh", value=True)
refresh_secs = st.sidebar.slider("Refresh every (sec)", 15, 300, 60, step=5, disabled=not auto)
if st.sidebar.button("🔄 Refresh now", width="stretch"):
    store = get_store()
    store["cooldown"] = 0.0
    for v in store["data"].values():
        v["ts"] = 0
st.sidebar.caption("Data: Yahoo Finance (can be delayed up to ~15 min). For education only — not financial advice.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
st.markdown('<div class="hero"><h1>Real-Time Stock Market Dashboard</h1>'
            '<p>Live prices, trends and technical indicators for the stocks you follow.</p></div>',
            unsafe_allow_html=True)

if not tickers:
    st.info("Select at least one ticker in the sidebar to get started.")
    st.stop()


@st.fragment(run_every=refresh_secs if auto else None)
def dashboard():
    data, errors, statuses = {}, {}, set()
    for sym in tickers:
        try:
            df, meta, status = get_data(sym, period, demo)
            data[sym] = (df, meta)
            statuses.add(status)
        except RateLimited:
            errors[sym] = "rate-limited by Yahoo Finance"
        except Exception as exc:
            errors[sym] = str(exc) or exc.__class__.__name__

    if not data:
        wait = max(0, int(get_store()["cooldown"] - time.time()))
        st.error("Couldn't load market data.")
        if any("rate-limited" in e for e in errors.values()):
            st.info(f"Yahoo Finance is temporarily rate-limiting requests from your network "
                    f"(retrying automatically in ~{wait}s). Turn on **🧪 Demo mode** in the sidebar to keep "
                    f"exploring with simulated data, or try again in a minute or two.")
        with st.expander("Details"):
            for s, e in errors.items():
                st.write(f"**{s}** — {e}")
        return

    status = "demo" if "demo" in statuses else "stale" if "stale" in statuses else "live"
    label = {"live": "Live", "stale": "Stale · rate-limited, showing last data", "demo": "Demo · simulated data"}[status]
    st.markdown(
        f'<span class="pill {status}"><span class="dot"></span>{label}</span> '
        f'<span style="color:#8b93a7;font-size:.8rem;margin-left:8px">'
        f'Updated {datetime.now():%H:%M:%S} · interval {PERIODS[period][1]} · '
        f'{"refresh every " + str(refresh_secs) + "s" if auto else "auto-refresh off"}</span>',
        unsafe_allow_html=True)
    for sym, err in errors.items():
        st.warning(f"{sym}: {err}")

    summaries = {s: summarize(df, meta, period) for s, (df, meta) in data.items()}
    tab_over, tab_tech, tab_data = st.tabs(["📊 Overview", "🔬 Technical analysis", "🗂 Raw data"])

    with tab_over:
        adv = sum(1 for s in summaries.values() if s["change"] >= 0)
        best = max(summaries, key=lambda k: summaries[k]["pct"])
        worst = min(summaries, key=lambda k: summaries[k]["pct"])
        k1, k2, k3, k4 = st.columns(4)
        k1.markdown(kpi("Tracked", len(summaries)), unsafe_allow_html=True)
        k2.markdown(kpi("Advancing / Declining", f"{adv} / {len(summaries) - adv}"), unsafe_allow_html=True)
        k3.markdown(kpi("Top performer", f"{best} {summaries[best]['pct']:+.2f}%", UP), unsafe_allow_html=True)
        k4.markdown(kpi("Weakest", f"{worst} {summaries[worst]['pct']:+.2f}%", DOWN), unsafe_allow_html=True)

        syms = list(data)
        for i in range(0, len(syms), 3):
            for col, sym in zip(st.columns(3), syms[i:i + 3]):
                col.markdown(stock_card(sym, summaries[sym], data[sym][0]["Close"]), unsafe_allow_html=True)

        st.markdown('<div class="section">Relative performance</div>', unsafe_allow_html=True)
        st.plotly_chart(comparison_chart({k: v[0]["Close"] for k, v in data.items()}), width="stretch")

    with tab_tech:
        sym = st.selectbox("Ticker", list(data), key="tech_symbol")
        df, _ = data[sym]
        if len(df) < 30:
            st.info("Not enough data points yet for indicators — try a longer period.")
        st.plotly_chart(technical_chart(df, sym, chart_style, overlays, show_rsi, show_macd, show_volume),
                        width="stretch")
        last_rsi = rsi(df["Close"]).iloc[-1]
        if pd.notna(last_rsi):
            ln, sg, _ = macd(df["Close"])
            state, scol = (("Overbought", DOWN) if last_rsi > 70 else ("Oversold", UP) if last_rsi < 30 else ("Neutral", "#fff"))
            trend, tcol = ("Bullish", UP) if ln.iloc[-1] > sg.iloc[-1] else ("Bearish", DOWN)
            a, b = st.columns(2)
            a.markdown(kpi("RSI (14)", f"{last_rsi:.1f} · {state}", scol), unsafe_allow_html=True)
            b.markdown(kpi("MACD signal", trend, tcol), unsafe_allow_html=True)

    with tab_data:
        sym = st.selectbox("Ticker", list(data), key="data_symbol")
        df = data[sym][0].copy()
        df["SMA 20"], df["EMA 20"], df["RSI 14"] = sma(df["Close"], 20), ema(df["Close"], 20), rsi(df["Close"])
        st.dataframe(df.sort_index(ascending=False).round(3), width="stretch", height=420)
        st.download_button("⬇ Download CSV", df.to_csv().encode(), f"{sym}_{period}.csv", "text/csv")


dashboard()
