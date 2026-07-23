# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Forex trading signal generator. It polls live currency-pair price data, computes technical indicators, and pushes BUY/SELL trade signals to a Telegram chat. A separate repository, `autobot2-auto-calibration-tweb`, reads those Telegram messages via web scraping and executes the trades automatically on Pocket Option — this repo only generates and sends signals, it does not place trades itself.

## Commands

```bash
pip install -r requirements.txt
python main.py
```

Running prompts interactively for which currency pair to track (`0` for all, or an index from the printed list), then loops indefinitely — poll, compute, signal, sleep to the next minute boundary — until `Ctrl+C`. There is no build step, lint config, or test suite in this repo.

## Architecture

Single-file Python application (`main.py`); no web framework is in use despite `fastapi`/`uvicorn` being listed in `requirements.txt`. Per loop iteration, for each tracked pair:

1. Fetch 1-minute candle data from the Twelve Data API (`/time_series`, 1000 bars, `America/New_York` timezone).
2. Compute 7 technical indicators: RSI, EMA20, MACD, Stochastic Oscillator, Bollinger Bands, CCI, ADX (via the `ta` library plus manual pandas EWM for EMA/MACD). Stochastic/CCI/ADX use the API's real per-candle `high`/`low` (not `close` standing in for all three, which was a bug present until this was fixed — check `df["high"]`/`df["low"]` are actually being read if these indicators look wrong again).
3. Each indicator independently votes BUY / SELL / HOLD (e.g. RSI 45/55, CCI ±75, ADX trend floor 16, a price-scaled MACD deadband instead of a bare zero-sign check). These are tightened from the original "intentionally loose" values.
4. A signal fires by majority vote (currently 6 of 7 indicators agreeing — this number has moved between 4 and 6 across manual edits; check the live `buy_count >=` / `sell_count >=` values in `main.py` rather than trusting this doc) **and** two confirmation guards, both required: at least one of MACD/ADX must agree with the majority's direction, AND a higher-timeframe check (5-min candles, resampled locally from the same 1-min bars — no extra API call — compared to their own 10-period EMA with a ±0.001% band) must also agree, or the candidate signal is downgraded to HOLD. The HTF margin was empirically tuned: a wider ±0.02% band left the HTF check reporting HOLD on most bars (killing nearly every signal), while ±0.001% keeps a workable share. With the current 6-of-7 vote threshold, this fires on roughly 0.5–2% of bars per pair — verified empirically. With a 4-of-7 vote threshold it was roughly 3.5–8.5% before the HTF guard was added. If signals seem too sparse or too frequent, re-measure empirically (see prior commits' verification scripts for the pattern) rather than guessing at new numbers.
5. On a BUY/SELL signal, `send_trade_signal()` posts a formatted message to Telegram via the Bot API (`sendMessage`).
6. Sleep until the top of the next minute and repeat.

### Git history context

Current `main` is the simplified 7-indicator version. Other branches on `origin` (`news`, `news-optimized`, `news-strict`, `hybrid`, `strict`, `strict-accurate`, `ML`, `8indicators`, `9indicators`, `20indicators`, `important-indicators`, `skipNotGood`, etc.) contain experiments not merged into `main` — including a news-API-based filter for volatile/unpredictable candles. If asked to work on news-based filtering or a different indicator count, check whether the relevant branch already has a working implementation before reimplementing from scratch.

**Before reactively tuning vote thresholds or indicator thresholds**, know that this has already been tried extensively, on this branch and others, without a real edge emerging: the `ML` branch has actual matched trade-outcome data (`data/merged_history.csv`, 383 real Pocket Option trades matched to logged signals) using 14 indicators plus higher-timeframe bias and a trained RandomForest model — considerably more sophisticated than `main`'s 7-indicator majority vote — and it measured a 53.8% win rate with net-negative P/L (binary-options payouts need roughly >55% win rate to break even). The other indicator-count/threshold-variant branches (`strict-accurate`, `strict`, `hybrid`, `skipNotGood`, `important-indicators`, `8/9/20indicators`) have no logged results at all. Treat this as evidence that the "combine classic TA indicators via majority vote" family of approaches likely caps out near breakeven on 1-minute forex regardless of specific thresholds — small-sample live win/loss counts (a handful to ~10 trades) are not a reliable basis for further tuning. If asked to improve win rate, prefer building real backtesting infrastructure over reactive threshold changes.

## Key Details

- **Data source**: Twelve Data REST API, 1-minute interval, requested with `dp=5` (5 decimal places) for full forex pip precision. This used to be `dp=2`, which silently rounded most non-JPY pairs (e.g. EUR/USD ~1.14xxx) to a flat, unmoving price — starving every indicator of real variance and making signals nearly impossible regardless of vote/threshold tuning. If signals ever go quiet again, check the raw API response's price precision before touching indicator thresholds. The app rotates across a pool of API keys (`API_KEYS` in `main.py`) to stay under free-tier rate limits: switching every 10 minutes on schedule, and immediately failing over to the next key on any request error (HTTP error, API error response, or network exception).
- **Known limitation — data lag**: measured live, the freshest 1-minute candle Twelve Data returns is consistently ~130 seconds (2+ minutes) behind wall-clock time on the free tier. `send_trade_signal()` stamps the message with the current wall-clock time, not the candle's timestamp, so every signal looks live but is actually reacting to a price move that's already ~2 minutes old. This is a likely contributor to poor win rate independent of indicator/threshold tuning; fixing it would require a lower-latency data source or paid plan, not a code change in this repo.
- **Notification**: Telegram Bot API — API key, bot token, and chat ID are hardcoded at the top of `main.py`.
- **Downstream execution**: signals sent to Telegram are consumed by the separate `autobot2-auto-calibration-tweb` repo, which scrapes the Telegram chat and executes trades on Pocket Option. Message format changes in `send_trade_signal()` will break that scraper, so coordinate wording/format changes with that repo.
- **Style**: keep logic in `main.py` unless splitting into modules is explicitly requested.

## Security Notes

API keys and the Telegram bot token are hardcoded in `main.py` (including several commented-out alternate API keys). These should be moved to environment variables before any public deployment or commit history cleanup.
