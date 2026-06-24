# AGENTS.md

## Project Overview

Forex trading signal generator that polls currency pair price data, computes technical indicators, and sends BUY/SELL trade signals to Telegram.

## Architecture

Single-file Python application (`main.py`) with no web framework in use despite FastAPI being listed in requirements. The app runs as a CLI loop:

1. User selects which currency pair(s) to track
2. Fetches 1-minute candle data from the Twelve Data API
3. Computes 7 technical indicators per pair: RSI, EMA20, MACD, Stochastic Oscillator, Bollinger Bands, CCI, ADX
4. Generates a signal via majority vote — 4+ indicators agreeing triggers a BUY or SELL
5. Sends signals to Telegram via bot API
6. Sleeps until the next minute boundary and repeats

## Key Details

- **Data source**: Twelve Data REST API (`/time_series`), 1-minute interval, 1000 bars
- **Signal logic**: Each indicator votes BUY/SELL/HOLD independently. A signal fires when 4 of 7 indicators agree on BUY or SELL.
- **Notification**: Telegram Bot API (`sendMessage`)
- **Timezone**: Prices requested in `America/New_York`; signal timestamps use the same zone

## Dependencies

- `requests` — HTTP calls to Twelve Data and Telegram
- `pandas` — price data manipulation
- `ta` — technical indicator calculations (RSI, Stochastic, Bollinger Bands, CCI, ADX)
- `fastapi`, `uvicorn` — listed in requirements but not currently used

## Running

```bash
pip install -r requirements.txt
python main.py
```

The script prompts for which pair to track (or all), then runs indefinitely until Ctrl+C.

## Security Notes

- API keys and Telegram bot tokens are hardcoded in `main.py`. These should be moved to environment variables or a `.env` file before any public deployment.

## Style

- Single-file structure — keep logic in `main.py` unless splitting into modules is explicitly requested
- Indicator thresholds are intentionally loose (e.g., RSI 48/52 instead of classic 30/70) — this is by design, not a bug
