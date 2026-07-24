# AGENTS.md

## Project Overview

Forex trading signal generator. It polls live currency-pair price data, computes technical indicators, and pushes BUY/SELL trade signals to a Telegram chat. A separate repository, `autobot2-auto-calibration-tweb`, reads those Telegram messages via web scraping and executes the trades automatically on Pocket Option — this repo only generates and sends signals, it does not place trades itself.

## Architecture

Single-file Python application (`main.py`); no web framework is in use despite `fastapi`/`uvicorn` being listed in `requirements.txt`. The entire decision pipeline — indicators, votes, guards — lives in one function, `evaluate_bar(df)`, which takes a DataFrame of 1-min candles and returns the vote breakdown, guard outcome, and final signal for the most recent bar. Both the live poll loop and the standalone `measure_fire_rate.py` replay script call this same function, so indicator/threshold changes only need to happen in one place. The interactive pair-selection prompt and the poll loop itself are guarded under `if __name__ == "__main__":`, so `main.py` can be safely imported by other scripts without triggering input prompts or network calls.

Per loop iteration, for each tracked pair:

1. Fetch 1-minute candle data from the Twelve Data API (`fetch_time_series()`, `/time_series`, 1000 bars, `America/New_York` timezone). See "Free-Tier API Limitations" below — this single call is the app's main constraint.
2. Compute 7 technical indicators inside `evaluate_bar()`: RSI, EMA20, MACD, Stochastic Oscillator, Bollinger Bands, CCI, ADX (via the `ta` library plus manual pandas EWM for EMA/MACD). Stochastic/CCI/ADX use the API's real per-candle `high`/`low` (not `close` standing in for all three, which was a bug present until this was fixed — check `df["high"]`/`df["low"]` are actually being read if these indicators look wrong again).
3. Each indicator independently votes BUY / SELL / HOLD (e.g. RSI 45/55, CCI ±75, ADX trend floor 16, a price-scaled MACD deadband instead of a bare zero-sign check). These are tightened from an original "intentionally loose" version.
4. **Vote, then two required guards** — in this order, each capable of downgrading a candidate to HOLD:
   - **Majority vote**: a candidate signal requires N of 7 indicators to agree (`buy_count >= N` / `sell_count >= N` in `evaluate_bar()`). This number has moved between 4 and 6 across manual edits — check the live values in `main.py` rather than trusting this doc.
   - **Trend guard**: the candidate is downgraded to HOLD unless at least one of MACD/ADX agrees with its direction.
   - **HTF guard**: the candidate is downgraded to HOLD unless a higher-timeframe check also agrees — 5-min candles, resampled locally from the same 1-min bars (no extra API call), compared to their own 10-period EMA with a ±0.001% band. This is a separate, required check, not folded into the trend guard's "at least one" OR.
   - `evaluate_bar()` exposes exactly which guard (if any) blocked a bar via `block_reason` (`no_majority` / `trend_guard` / `htf_guard` / `None` if it fired), and the live console line prints it directly as `Block: ...` alongside `HTF: ...` — check this field first when a bar doesn't fire despite a high vote count.
   - Measured empirically across all 22 pairs with a 6-of-7 vote threshold (`measure_fire_rate.py`, a no-lookahead bar-by-bar replay over each pair's fetched history — run it with no args to re-measure): candidates=1.3% of bars, blocked_trend=0.0% of candidates, blocked_htf=77.5% of candidates, fired=0.29% of bars. The HTF guard is doing essentially all of the suppression — the trend guard almost never blocks anything on its own.
5. On a BUY/SELL signal, `send_trade_signal()` posts a formatted message to Telegram via the Bot API (`sendMessage`).
6. Sleep until the top of the next minute and repeat.

## Free-Tier API Limitations

This app's non-obvious complexity — the 9-key rotation pool, `dp=5`, the ~130s lag — exists almost entirely because of Twelve Data's free-tier constraints, not because of the signal logic itself.

- **A single request can exceed its own key's rate limit.** Twelve Data's free tier caps each key at **8 API credits/minute**. One `/time_series` call with `outputsize=1000` costs **10 credits** — one full-history fetch on one key already exceeds that key's per-minute allowance. Confirmed live during a `measure_fire_rate.py` run: `HTTP 429 "You have run out of API credits for the current minute. 10 API credits were used, with the current limit being 8."` fired for 6 of the 22 tracked pairs in a single run. Expected, routine behavior any time more than ~1 pair is fetched per minute on one key — not a rare edge case.
- **The key-rotation pool (`API_KEYS`, `main.py`) is the actual fix, not a nice-to-have.** 9 free-tier keys, rotated on a 10-minute schedule (`ROTATION_INTERVAL_SECONDS`, `get_active_key()`) and immediately on any request failure (`advance_key()`). `fetch_time_series()` tries every key in the pool once per symbol before giving up; if all 9 are rate-limited at once for the same pair, that pair is silently skipped for the cycle (`fetch_time_series` returns `None`) with no retry until the next minute's iteration.
- **Price precision (`dp=5`)**: requested at 5 decimal places for full forex pip/pipette precision. This used to be `dp=2`, which silently rounded most non-JPY pairs (e.g. EUR/USD ~1.14xxx) to a flat, unmoving price — starving every indicator of real variance and making signals nearly impossible, with no error from the API to indicate anything was wrong.
- **Data lag (~130s)**: the freshest 1-minute candle Twelve Data returns is consistently ~130 seconds behind wall-clock time on the free tier. `send_trade_signal()` stamps the message with wall-clock time, not the candle's timestamp, so every signal looks live but is reacting to a price move that's already ~2 minutes old.
- **`outputsize=1000` refetches the same window every poll.** Each 60-second cycle requests the full most-recent 1000 bars again rather than fetching only the newest bar and appending it locally — credit-inefficient but avoids maintaining local state across restarts.

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

The script prompts for which pair to track (or all), then runs indefinitely until Ctrl+C. There is no build step, lint config, or test suite in this repo.

## Security Notes

- API keys and Telegram bot tokens are hardcoded in `main.py`. These should be moved to environment variables or a `.env` file before any public deployment.

## Style

- Single-file structure — keep logic in `main.py` unless splitting into modules is explicitly requested.
