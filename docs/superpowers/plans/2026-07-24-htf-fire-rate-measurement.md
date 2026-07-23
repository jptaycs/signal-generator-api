# HTF Fire-Rate Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the signal pipeline into a reusable `evaluate_bar(df)` function, build a committed `measure_fire_rate.py` script that replays it bar-by-bar over historical data with no lookahead to report fire rate broken down by which guard blocked each candidate, and surface HTF status in the live console output.

**Architecture:** `main.py`'s inline indicator/vote/guard logic becomes a standalone function that both the live poll loop and the new measurement script call. The interactive pair-selection prompt and the poll loop move under `if __name__ == "__main__":` so the module can be imported without side effects.

**Tech Stack:** Python, pandas, `ta` library, Twelve Data REST API (via existing `fetch_time_series`).

## Global Constraints

- No test framework exists in this repo (per `CLAUDE.md`: "no build step, lint config, or test suite"). Verification steps below use `python -m py_compile` for syntax checks and standalone smoke scripts (run via `python3 -c` or a throwaway file, not committed) instead of pytest — this matches the design spec's "verification is empirical/manual, consistent with existing project practice."
- `evaluate_bar` must not change any indicator math, threshold, or vote count — behavior-preserving extraction only (per spec section 1).
- Keep all pipeline logic in `main.py`; only the new measurement script is a separate file (per spec's "out of scope: splitting main.py into multiple modules").
- Do not change any threshold, vote count, or guard margin based on measurement results — this plan only builds the measurement tool (per spec's "out of scope").

---

### Task 1: Extract `evaluate_bar(df)` and guard the interactive setup

**Files:**
- Modify: `main.py`

**Interfaces:**
- Produces: `evaluate_bar(df: pd.DataFrame) -> dict` with keys `price`, `rsi_status`, `ema_status`, `macd_status`, `stoch_status`, `bb_status`, `cci_status`, `adx_status`, `htf_status`, `candidate_signal` (`"BUY"`/`"SELL"`/`None`), `block_reason` (`"insufficient_history"`/`"no_majority"`/`"trend_guard"`/`"htf_guard"`/`None`), `signal` (string, e.g. `"BUY (score=6)"` or `"HOLD (score=4)"`), `buy_count`, `sell_count`, `hold_count`, `last_rsi`, `ema_20`, `macd`, `stoch_k`, `bb_high`, `bb_low`, `last_cci`, `last_adx`. `df` must be ascending-sorted by `datetime` with float `high`/`low`/`close` columns — same shape the existing loop already builds.
- Consumes: nothing new — uses the same `ta` library calls and threshold constants already in `main.py`.

- [ ] **Step 1: Insert `evaluate_bar(df)` after `send_trade_signal`, before the `pairs = [...]` list**

In `main.py`, immediately after the line `print("Failed to send Telegram message:", response.text)` (end of `send_trade_signal`) and before `pairs = [`, insert:

```python

def evaluate_bar(df):
    price = df["close"].iloc[-1]

    # RSI (Relative Strength Index) — momentum indicator
    rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
    rsi_values = rsi_indicator.rsi()
    last_rsi = rsi_values.iloc[-1] if len(rsi_values) > 0 else None

    # EMA20 — 20-period trend indicator, compared against current price
    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None

    # MACD — difference between the 12- and 26-period EMAs (trend/momentum)
    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    macd = (ema_12 - ema_26).iloc[-1] if len(df) >= 26 else None

    # Stochastic Oscillator
    stoch = ta.momentum.StochasticOscillator(
        high=df["high"], low=df["low"], close=df["close"], window=14, smooth_window=3
    )
    stoch_k = stoch.stoch().iloc[-1] if len(df) > 0 else None

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
    bb_high = bb.bollinger_hband().iloc[-1] if len(df) > 0 else None
    bb_low = bb.bollinger_lband().iloc[-1] if len(df) > 0 else None

    # Commodity Channel Index (CCI)
    cci = ta.trend.CCIIndicator(high=df["high"], low=df["low"], close=df["close"], window=20)
    last_cci = cci.cci().iloc[-1] if len(df) > 0 else None

    # Average Directional Index (ADX)
    adx = ta.trend.ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=14)
    last_adx = adx.adx().iloc[-1] if len(df) > 0 else None

    # Higher-timeframe trend, derived by resampling the same 1-min bars we
    # already have (no extra API call) into 5-min candles. Used as a
    # confirmation filter below so a 1-min majority can't fire against the
    # larger trend. Margin is deliberately small (0.001%, not the 0.02% used
    # for the 1-min EMA check) — a wider margin left this HTF check reporting
    # HOLD on most bars, which silently killed nearly every signal when
    # empirically measured.
    df_htf = df.set_index("datetime").resample("5min").agg({"close": "last"}).dropna()
    htf_ema = df_htf["close"].ewm(span=10, adjust=False).mean()
    if len(df_htf) >= 10:
        last_htf_close = df_htf["close"].iloc[-1]
        last_htf_ema = htf_ema.iloc[-1]
        htf_status = "BUY" if last_htf_close > last_htf_ema * 1.00001 else "SELL" if last_htf_close < last_htf_ema * 0.99999 else "HOLD"
    else:
        htf_status = "HOLD"

    if last_rsi is None or ema_20 is None or macd is None:
        return {
            "price": price,
            "rsi_status": None, "ema_status": None, "macd_status": None,
            "stoch_status": None, "bb_status": None, "cci_status": None, "adx_status": None,
            "htf_status": htf_status,
            "candidate_signal": None,
            "block_reason": "insufficient_history",
            "signal": "HOLD",
            "buy_count": 0, "sell_count": 0, "hold_count": 0,
            "last_rsi": last_rsi, "ema_20": ema_20, "macd": macd,
            "stoch_k": stoch_k, "bb_high": bb_high, "bb_low": bb_low,
            "last_cci": last_cci, "last_adx": last_adx,
        }

    # Each of the 7 indicators independently votes BUY/SELL/HOLD.
    rsi_status = "BUY" if last_rsi < 45 else "SELL" if last_rsi > 55 else "HOLD"
    ema_status = "BUY" if price > ema_20 * 1.0002 else "SELL" if price < ema_20 * 0.9998 else "HOLD"
    # MACD deadband (scaled to price) instead of a bare sign check, so small
    # oscillations around zero vote HOLD rather than always BUY/SELL.
    macd_status = "BUY" if macd > price * 0.00002 else "SELL" if macd < -price * 0.00002 else "HOLD"
    stoch_status = "BUY" if stoch_k is not None and stoch_k < 35 else "SELL" if stoch_k is not None and stoch_k > 65 else "HOLD"
    bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.0002 else "SELL" if bb_high is not None and price >= bb_high * 0.9998 else "HOLD"
    cci_status = "BUY" if last_cci is not None and last_cci < -75 else "SELL" if last_cci is not None and last_cci > 75 else "HOLD"
    adx_status = "BUY" if last_adx is not None and last_adx > 16 and macd > 0 else "SELL" if last_adx is not None and last_adx > 16 and macd < 0 else "HOLD"

    # Majority vote: a candidate signal needs at least 6 of 7 indicators to agree.
    statuses = [rsi_status, ema_status, macd_status, stoch_status, bb_status, cci_status, adx_status]
    buy_count = statuses.count("BUY")
    sell_count = statuses.count("SELL")
    hold_count = statuses.count("HOLD")

    if buy_count >= 6:
        candidate_signal = "BUY"
    elif sell_count >= 6:
        candidate_signal = "SELL"
    else:
        candidate_signal = None

    # Guard attribution: no_majority (vote never reached 6-of-7), trend_guard
    # (neither MACD nor ADX backed the candidate direction), htf_guard (the
    # 5-min resampled trend disagreed), or None (fired — passed both guards).
    if candidate_signal is None:
        block_reason = "no_majority"
    elif not (macd_status == candidate_signal or adx_status == candidate_signal):
        block_reason = "trend_guard"
    elif htf_status != candidate_signal:
        block_reason = "htf_guard"
    else:
        block_reason = None

    if block_reason is None:
        signal = f"{candidate_signal} (score={buy_count if candidate_signal == 'BUY' else sell_count})"
    else:
        signal = f"HOLD (score={hold_count})"

    return {
        "price": price,
        "rsi_status": rsi_status, "ema_status": ema_status, "macd_status": macd_status,
        "stoch_status": stoch_status, "bb_status": bb_status, "cci_status": cci_status, "adx_status": adx_status,
        "htf_status": htf_status,
        "candidate_signal": candidate_signal,
        "block_reason": block_reason,
        "signal": signal,
        "buy_count": buy_count, "sell_count": sell_count, "hold_count": hold_count,
        "last_rsi": last_rsi, "ema_20": ema_20, "macd": macd,
        "stoch_k": stoch_k, "bb_high": bb_high, "bb_low": bb_low,
        "last_cci": last_cci, "last_adx": last_adx,
    }

```

- [ ] **Step 2: Delete the now-duplicated inline computation from the poll loop and call `evaluate_bar` instead**

Replace the entire block from `df["high"] = df["high"].astype(float)` through the end of the guard `if` block (originally ending at `signal = f"HOLD (score={hold_count})"`) with:

```python
                df["high"] = df["high"].astype(float)
                df["low"] = df["low"].astype(float)
                df["close"] = df["close"].astype(float)

                result = evaluate_bar(df)
                price = result["price"]
                signal = result["signal"]
```

- [ ] **Step 3: Replace the print line and str-formatting block to use `result` and add HTF/block visibility**

Replace the block from `ema_str = f"{ema_20:.5f}"...` through the existing `print(f"{symbol} | Price: ...")` line with:

```python
                rsi_str = f"{result['last_rsi']:.2f}" if result["last_rsi"] is not None else "N/A"
                ema_str = f"{result['ema_20']:.5f}" if result["ema_20"] is not None else "N/A"
                macd_str = f"{result['macd']:.5f}" if result["macd"] is not None else "N/A"
                stoch_str = f"{result['stoch_k']:.2f}" if result["stoch_k"] is not None else "N/A"
                bb_high_str = f"{result['bb_high']:.5f}" if result["bb_high"] is not None else "N/A"
                bb_low_str = f"{result['bb_low']:.5f}" if result["bb_low"] is not None else "N/A"
                cci_str = f"{result['last_cci']:.2f}" if result["last_cci"] is not None else "N/A"
                adx_str = f"{result['last_adx']:.2f}" if result["last_adx"] is not None else "N/A"
                block_str = result["block_reason"] if result["block_reason"] is not None else "fired"

                print(
                    f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({result['rsi_status']}) | "
                    f"EMA20: {ema_str} ({result['ema_status']}) | MACD: {macd_str} ({result['macd_status']}) | "
                    f"Stoch: {stoch_str} ({result['stoch_status']}) | BB: Low {bb_low_str}, High {bb_high_str} ({result['bb_status']}) | "
                    f"CCI: {cci_str} ({result['cci_status']}) | ADX: {adx_str} ({result['adx_status']}) | "
                    f"HTF: {result['htf_status']} | Signal: {signal} | Block: {block_str} | "
                    f"Breakdown: BUY={result['buy_count']}, SELL={result['sell_count']}, HOLD={result['hold_count']}"
                )
```

- [ ] **Step 4: Move the interactive prompt, pair filtering, and `price_history` init under `if __name__ == "__main__":`**

Move these lines (currently module-level, before `if __name__ == "__main__":`):

```python
print("Available pairs:")
for i, p in enumerate(pairs, start=1):
    print(f"{i}. {p}")
# Ask user which pair to track by index, or all
choice = input(f"What pair do you want to track? (0 for All, 1-{len(pairs)}): ")
try:
    choice = int(choice)
except ValueError:
    print(f"Invalid input. Please enter an integer between 0 and {len(pairs)}.")
    exit(1)

if choice == 0:
    # Track all pairs
    pass
elif 1 <= choice <= len(pairs):
    pairs = [pairs[choice - 1]]
else:
    print(f"Choice must be between 0 and {len(pairs)}.")
    exit(1)

print(f"Tracking the following pairs: {pairs}")

price_history = {pair: [] for pair in pairs}
```

so they become the first lines inside `if __name__ == "__main__":`, immediately before the existing `try:` that starts the poll loop. The `pairs = [...]` list definition itself (the 22-pair list) stays at module level, unchanged, so it's importable without side effects.

- [ ] **Step 5: Syntax-check**

Run: `python -m py_compile main.py`
Expected: no output, exit code 0.

- [ ] **Step 6: Smoke-test `evaluate_bar` with a synthetic DataFrame (no network, no interactive input)**

Run:
```bash
python3 -c "
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import main

rng = np.random.default_rng(42)
n = 120
base = 1.10000
prices = base + np.cumsum(rng.normal(0, 0.0003, n))
start = datetime(2026, 1, 1, 9, 0)
df = pd.DataFrame({
    'datetime': [start + timedelta(minutes=i) for i in range(n)],
    'high': prices + 0.0002,
    'low': prices - 0.0002,
    'close': prices,
})

result = main.evaluate_bar(df)
expected_keys = {
    'price', 'rsi_status', 'ema_status', 'macd_status', 'stoch_status',
    'bb_status', 'cci_status', 'adx_status', 'htf_status', 'candidate_signal',
    'block_reason', 'signal', 'buy_count', 'sell_count', 'hold_count',
    'last_rsi', 'ema_20', 'macd', 'stoch_k', 'bb_high', 'bb_low', 'last_cci', 'last_adx',
}
assert set(result.keys()) == expected_keys, f'key mismatch: {set(result.keys()) ^ expected_keys}'
assert result['block_reason'] in {None, 'no_majority', 'trend_guard', 'htf_guard', 'insufficient_history'}
print('OK', result['signal'], result['block_reason'], result['htf_status'])
"
```
Expected: prints `OK <signal string> <block_reason or None> <htf status>` with no traceback. Importing `main` must not prompt for input or make a network call — if it hangs waiting for stdin, Step 4 wasn't applied correctly.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
Extract evaluate_bar() from the poll loop and add HTF/guard visibility

Pulls the indicator/vote/guard pipeline out of the inline loop body
into a reusable evaluate_bar(df) function, so it can be called from
both the live loop and a future measurement script without
duplicating logic. Also moves the interactive pair-selection prompt
under __main__ so the module is safely importable, and adds
htf_status plus a block_reason (no_majority/trend_guard/htf_guard)
to the live console line for visibility into why a bar HOLDs.

Behavior-preserving: same indicator math, thresholds, and vote
counts as before.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `measure_fire_rate.py` — historical replay script

**Files:**
- Create: `measure_fire_rate.py`

**Interfaces:**
- Consumes: `evaluate_bar(df) -> dict` and `fetch_time_series(symbol) -> dict | None` and `pairs: list[str]`, all from `main` (Task 1).
- Produces: a standalone CLI script; no other file depends on it.

- [ ] **Step 1: Write the script**

Create `measure_fire_rate.py`:

```python
import sys

import pandas as pd

from main import evaluate_bar, fetch_time_series, pairs as all_pairs

WARMUP_BARS = 60


def replay_pair(symbol):
    raw = fetch_time_series(symbol)
    if raw is None:
        print(f"{symbol:8s} | SKIPPED (all API keys failed)")
        return None

    df = pd.DataFrame(raw["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    if len(df) <= WARMUP_BARS:
        print(f"{symbol:8s} | SKIPPED (only {len(df)} bars, need > {WARMUP_BARS})")
        return None

    bars_evaluated = 0
    candidates = 0
    blocked_trend = 0
    blocked_htf = 0
    fired_buy = 0
    fired_sell = 0

    for i in range(WARMUP_BARS, len(df)):
        window = df.iloc[: i + 1]
        result = evaluate_bar(window)
        if result["block_reason"] == "insufficient_history":
            continue

        bars_evaluated += 1
        if result["candidate_signal"] is not None:
            candidates += 1
            if result["block_reason"] == "trend_guard":
                blocked_trend += 1
            elif result["block_reason"] == "htf_guard":
                blocked_htf += 1
            elif result["block_reason"] is None:
                if result["candidate_signal"] == "BUY":
                    fired_buy += 1
                else:
                    fired_sell += 1

    fired = fired_buy + fired_sell
    candidate_rate = candidates / bars_evaluated * 100 if bars_evaluated else 0.0
    fired_rate = fired / bars_evaluated * 100 if bars_evaluated else 0.0

    print(
        f"{symbol:8s} | bars={bars_evaluated:4d} | "
        f"candidates={candidates:3d} ({candidate_rate:4.1f}%) | "
        f"blocked_trend={blocked_trend:3d} | blocked_htf={blocked_htf:3d} | "
        f"fired={fired:3d} (BUY={fired_buy} SELL={fired_sell}, {fired_rate:.2f}%)"
    )

    return {
        "bars_evaluated": bars_evaluated,
        "candidates": candidates,
        "blocked_trend": blocked_trend,
        "blocked_htf": blocked_htf,
        "fired_buy": fired_buy,
        "fired_sell": fired_sell,
    }


def main():
    symbols = [sys.argv[1]] if len(sys.argv) > 1 else all_pairs

    totals = {
        "bars_evaluated": 0, "candidates": 0,
        "blocked_trend": 0, "blocked_htf": 0,
        "fired_buy": 0, "fired_sell": 0,
    }

    for symbol in symbols:
        stats = replay_pair(symbol)
        if stats is None:
            continue
        for key in totals:
            totals[key] += stats[key]

    bars = totals["bars_evaluated"]
    if bars == 0:
        print("No bars evaluated across any pair.")
        return

    candidates = totals["candidates"]
    fired = totals["fired_buy"] + totals["fired_sell"]
    candidate_rate = candidates / bars * 100
    fired_rate = fired / bars * 100
    trend_share = totals["blocked_trend"] / candidates * 100 if candidates else 0.0
    htf_share = totals["blocked_htf"] / candidates * 100 if candidates else 0.0

    print(
        f"\nAGGREGATE | bars={bars} | candidates={candidate_rate:.1f}% | "
        f"blocked_trend={trend_share:.1f}% of candidates | "
        f"blocked_htf={htf_share:.1f}% of candidates | "
        f"fired={fired_rate:.2f}% of bars (BUY={totals['fired_buy']} SELL={totals['fired_sell']})"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax-check**

Run: `python -m py_compile measure_fire_rate.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Smoke-test against one live pair**

Run: `python3 measure_fire_rate.py EUR/USD`
Expected: one `EUR/USD | bars=... | candidates=... | ...` line followed by an `AGGREGATE` line, both with real numbers (not exceptions). This hits the live Twelve Data API via the existing key pool — if it prints `SKIPPED (all API keys failed)`, check network connectivity before treating Task 2 as broken (not a code bug).

- [ ] **Step 4: Commit**

```bash
git add measure_fire_rate.py
git commit -m "$(cat <<'EOF'
Add measure_fire_rate.py: no-lookahead replay of the signal pipeline

Fetches each pair's 1000-bar history once and replays evaluate_bar
over the expanding prefix ending at each bar, so every simulated
decision only sees data that would have existed at that point in
time. Reports fire rate broken down by which guard (trend vs HTF)
blocked each 6-of-7 candidate, closing the gap where CLAUDE.md
referenced a "verification script" that was never actually
committed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Run the measurement and update documentation

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `measure_fire_rate.py` output (Task 2).

- [ ] **Step 1: Run the full measurement across all pairs**

Run: `python3 measure_fire_rate.py`
Expected: 22 per-pair lines plus one `AGGREGATE` line. This takes on the order of 1–3 minutes (indicator recomputation over a growing window, 22 pairs). Record the full output.

- [ ] **Step 2: Compare against the existing CLAUDE.md claim**

`CLAUDE.md`'s Architecture section currently states: *"With the current 6-of-7 vote threshold, this fires on roughly 0.5–2% of bars per pair — verified empirically."* Compare the new `AGGREGATE fired=X% of bars` figure and the `blocked_trend`/`blocked_htf` shares against this. If the fired-rate range still roughly matches, keep the existing sentence; if it differs meaningfully, update the number.

- [ ] **Step 3: Update CLAUDE.md**

In the Architecture section's step 4 paragraph (the one describing the majority vote and confirmation guards), after the existing sentence ending "...verified empirically." add:

```markdown
 A committed replay script, `measure_fire_rate.py`, reproduces this measurement on demand (no-lookahead replay over each pair's fetched history) and additionally breaks down *why* candidates get blocked — by the trend guard (MACD/ADX) vs. the HTF guard — rather than only reporting the final fire rate. Last run: [fill in AGGREGATE line from Step 1, e.g. "candidates=X.X% of bars, blocked_trend=X.X% of candidates, blocked_htf=X.X% of candidates, fired=X.XX% of bars"].
```

(Fill in the bracketed part with the actual `AGGREGATE` line captured in Step 1 — this is real measured data, not a placeholder to leave unfilled.)

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
Document measured HTF/trend guard fire-rate breakdown

Records the measure_fire_rate.py output and points to it as the
reusable tool for re-measuring fire rate, replacing the prior
reference to a "verification script" that didn't actually exist
in the repo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (extract evaluate_bar) → Task 1 Steps 1–3. §2 (guard interactive prompt) → Task 1 Step 4. §3 (measure_fire_rate.py) → Task 2. §4 (live-loop visibility) → Task 1 Step 3. §5 (documentation) → Task 3. Error handling (skip failed pairs, exclude insufficient-history bars) → Task 2 Step 1 (`raw is None` check, `block_reason == "insufficient_history"` skip). Testing/verification → each task's smoke-test/run steps.
- **Placeholder scan:** Task 3 Step 3 has a bracketed fill-in, but it's explicitly flagged as real measured data to be filled from Step 1's actual output, not an unresolved design gap — consistent with the spec's own "Testing/Verification" section, which expects this number to come from an actual run.
- **Type consistency:** `evaluate_bar` return dict keys are defined once in Task 1 Step 1 and referenced identically (`result["..."]`) in Task 1 Step 3 and throughout Task 2's `replay_pair`. `block_reason` values (`None`, `"no_majority"`, `"trend_guard"`, `"htf_guard"`, `"insufficient_history"`) are consistent across both files.
