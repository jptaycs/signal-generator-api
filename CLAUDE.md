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
2. Compute 7 technical indicators: RSI, EMA20, MACD, Stochastic Oscillator, Bollinger Bands, CCI, ADX (via the `ta` library plus manual pandas EWM for EMA/MACD).
3. Each indicator independently votes BUY / SELL / HOLD (e.g. RSI 43/57, CCI ±85, ADX trend floor 17, a price-scaled MACD deadband instead of a bare zero-sign check). These were tightened from the original "intentionally loose" values, then eased back partway after the first pass proved too strict and left signals stuck on HOLD.
4. A signal fires by majority vote (5 of 7 indicators agreeing — raised from 4 after a bare 4-of-7 majority proved to fire on too many losing setups) **and** a trend-confirmation guard: at least one of MACD/ADX must also agree with that majority's direction, or the candidate signal is downgraded to HOLD. This exists to stop mean-reversion indicators (RSI, Stochastic, Bollinger, CCI) from outvoting a fully absent or conflicting trend, without requiring both trend indicators to agree simultaneously.
5. On a BUY/SELL signal, `send_trade_signal()` posts a formatted message to Telegram via the Bot API (`sendMessage`).
6. Sleep until the top of the next minute and repeat.

### Git history context

Current `main` is the simplified 7-indicator version. Other branches on `origin` (`news`, `news-optimized`, `news-strict`, `hybrid`, `strict`, `ML`, `8indicators`, `9indicators`, `20indicators`, etc.) contain experiments not merged into `main` — including a news-API-based filter for volatile/unpredictable candles. If asked to work on news-based filtering or a different indicator count, check whether the relevant branch already has a working implementation before reimplementing from scratch.

## Key Details

- **Data source**: Twelve Data REST API, 1-minute interval. The app rotates across a pool of API keys (`API_KEYS` in `main.py`) to stay under free-tier rate limits: switching every 10 minutes on schedule, and immediately failing over to the next key on any request error (HTTP error, API error response, or network exception).
- **Notification**: Telegram Bot API — API key, bot token, and chat ID are hardcoded at the top of `main.py`.
- **Downstream execution**: signals sent to Telegram are consumed by the separate `autobot2-auto-calibration-tweb` repo, which scrapes the Telegram chat and executes trades on Pocket Option. Message format changes in `send_trade_signal()` will break that scraper, so coordinate wording/format changes with that repo.
- **Style**: keep logic in `main.py` unless splitting into modules is explicitly requested.

## Security Notes

API keys and the Telegram bot token are hardcoded in `main.py` (including several commented-out alternate API keys). These should be moved to environment variables before any public deployment or commit history cleanup.
