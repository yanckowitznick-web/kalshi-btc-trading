
# Kalshi BTC 15-minute Paper Trading Desk

A local Streamlit app that scans Kalshi's public `KXBTC15M` markets, reads public order books, calculates transparent experimental BTC momentum indicators, and lets you manually approve **simulated paper trades**.

## Safety / scope

- Paper trading only. There is no live order placement code.
- No Kalshi API key or private key is needed for the public market-data endpoints used here.
- Coinbase BTC-USD 1-minute candles are used as a spot-price proxy. They are **not** the CF Benchmarks BRTI settlement index used by Kalshi.
- The score-to-probability display is an uncalibrated research heuristic, not a validated probability or claim of an edge.
- Paper fills assume the displayed ask and do not model queue position, latency, partial fills, or all market-specific fee differences.
- Do not use real money based on this dashboard. Collect data and validate out-of-sample first.

Kalshi explains that its 15-minute crypto markets compare the average CF Benchmarks index value during the 60 seconds before the market closes with the average during the 60 seconds before it opens. Always read the individual market rules.

## Install on macOS

1. Download and unzip this project.
2. Install Python 3.10 or newer. Python 3.11 is recommended.
3. Open Terminal and `cd` into the unzipped `kalshi_btc_paper_trader` folder.
4. Run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

5. Your browser should open the dashboard at `http://localhost:8501`.

You can also double-click `run_mac.command`. If macOS blocks it, right-click > Open, or run it from Terminal.

## How to use

- The app refreshes public market data every 10 seconds by default.
- Review the current market, order-book asks, BTC proxy indicators, time remaining, and trade filters.
- A trade button is enabled only when the initial filters pass.
- You still choose YES/NO and approve each paper trade.
- The SQLite trade log is stored locally in `paper_trades.sqlite3`.
- Settled outcomes are reconciled against Kalshi's public market endpoint when available.
- Download the trade history CSV from the dashboard.

## Initial risk controls

The default bankroll is $75, with a 1.5% maximum cost per paper trade. The daily loss cap is not yet enforced automatically because this first build is paper-only; monitor realized P/L and stop testing if results deteriorate.

## Important next research steps

Before considering any live execution:
1. Ingest the actual CF Benchmarks BRTI value stream or the exact official index series available under Kalshi's API docs.
2. Save timestamped market quotes, order books, BRTI observations, and settlement outcomes.
3. Build a proper historical dataset and out-of-sample backtest with realistic bid/ask, fees, and latency.
4. Calibrate predicted probabilities (e.g., isotonic/logistic calibration) and evaluate Brier score, log loss, expected value, and drawdowns.
5. Test the strategy in paper mode over a meaningful sample, across different volatility regimes.
6. Review Kalshi's current fee schedule and individual market rules.

## API references

- Kalshi public market-data quick start: https://docs.kalshi.com/getting_started/quick_start_market_data
- Kalshi crypto market settlement explanation: https://help.kalshi.com/en/articles/13823838-crypto-markets
- Kalshi current fee schedule: https://kalshi.com/fee-schedule
