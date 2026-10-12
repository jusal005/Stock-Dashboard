# Real-Time Stock Market Dashboard

Live prices, candlestick charts and technical indicators (SMA/EMA, Bollinger Bands, VWAP, RSI, MACD)
for any tickers you choose. Built with Streamlit, Pandas, Plotly and Requests.

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Notes
- Data source: Yahoo Finance's public chart endpoint (no API key). Use `.NS` / `.BO` suffixes for Indian stocks (e.g. `RELIANCE.NS`).
- Auto-refresh uses `st.fragment(run_every=...)`, so it needs Streamlit >= 1.50.
- To swap in Alpha Vantage / Finnhub / Polygon, replace only `fetch_history()` so it returns an OHLCV DataFrame + a meta dict.
