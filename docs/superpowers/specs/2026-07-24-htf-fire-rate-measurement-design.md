# HTF Fire-Rate Measurement Design

## Problem

The higher-timeframe (HTF) trend-confirmation guard (`main.py`, added in commit `c6de585`) was tuned by an ad hoc empirical check that was never captured as a reusable script. `CLAUDE.md` references "prior commits' verification scripts for the pattern" as the way to re-measure fire rate if signals seem too sparse or too frequent, but no such script actually exists in git history — the methodology was applied once, by hand, and discarded. There's currently no way to know, without re-deriving it from scratch, how much of the HOLD rate is specifically attributable to the HTF guard versus the pre-existing trend-confirmation (MACD/ADX) guard versus simply not reaching a 6-of-7 majority at all.

## Goal

Build a reusable, committed measurement script that replays the full signal pipeline bar-by-bar over each pair's already-available 1000-bar history with no lookahead, and reports fire rate broken down by which guard (if any) blocked each candidate. Also surface HTF status and block reason in the live loop's console output, so future "signals seem off" investigations have visibility without needing to run the script.

## Design

### 1. Extract `evaluate_bar(df)` in `main.py`

The indicator computation, per-indicator voting, majority-vote, and guard logic currently inline in the polling loop (`main.py:178–278`) is extracted into a function:

```python
def evaluate_bar(df):
    ...
    return {
        "price": price,
        "rsi_status": ..., "ema_status": ..., "macd_status": ...,
        "stoch_status": ..., "bb_status": ..., "cci_status": ..., "adx_status": ...,
        "htf_status": htf_status,
        "candidate_signal": candidate_signal,   # "BUY" / "SELL" / None
        "block_reason": block_reason,           # "no_majority" / "trend_guard" / "htf_guard" / None
        "signal": signal,                       # unchanged string format, e.g. "BUY (score=6)"
        "buy_count": buy_count, "sell_count": sell_count, "hold_count": hold_count,
    }
```

`block_reason` is derived as:
- `"no_majority"` — neither `buy_count >= 6` nor `sell_count >= 6`.
- `"trend_guard"` — a candidate exists but neither `macd_status` nor `adx_status` agrees with it.
- `"htf_guard"` — a candidate passed the trend guard but `htf_status` disagrees.
- `None` — the candidate fired (passed both guards).

This is a behavior-preserving extraction: the live loop calls `evaluate_bar(df)` once per poll exactly where the inline logic used to run, using the same 1000-bar `df` it already fetches. No change to indicator math, thresholds, or vote counts.

### 2. Guard the interactive prompt

The pair-selection `input()` prompt (`main.py:136–156`) currently executes at module import time, which would block any script that imports `main.py` to reuse `evaluate_bar`. It moves under `if __name__ == "__main__":`, alongside the existing polling loop. Behavior when running `python main.py` directly is unchanged.

### 3. `measure_fire_rate.py` (new, committed)

For each pair (or a single pair passed as an optional CLI arg, for faster iteration):
1. Fetch the 1000-bar 1-min history once via `fetch_time_series` (imported from `main.py`).
2. For each bar index `i` from a warmup floor of 60 (enough history for BB/ADX/RSI windows and 10 HTF 5-min buckets) to the end, call `evaluate_bar(df.iloc[:i+1])`. This is the "no lookahead" replay: at each simulated point in time, `evaluate_bar` only sees bars up to that point — structurally identical to what a live poll would have seen if it had been running since bar 0.
3. Tally per pair: bars evaluated, candidates raised, blocked by `trend_guard`, blocked by `htf_guard`, fired BUY, fired SELL, and the corresponding rates.
4. Print a per-pair line, then an aggregate summary across all pairs evaluated.

Expected runtime: recomputing indicators over a growing window per bar is roughly O(n) `evaluate_bar` calls per pair (n≈940 after warmup), each with its own indicator-computation overhead. Across 22 pairs this is expected to take on the order of 1–3 minutes — acceptable for a script run occasionally, not on every poll.

Sample output shape:
```
AUD/CAD    | bars=940 | candidates=41 (4.4%) | blocked_trend=12 | blocked_htf=21 | fired=8 (BUY=5 SELL=3, 0.85%)
...
AGGREGATE  | bars=20680 | candidates=4.1% | blocked_trend=27% of candidates | blocked_htf=51% of candidates | fired=0.9% of bars
```

### 4. Live-loop visibility

The existing per-bar console print line (`main.py:289`) gains `htf_status` and, when the signal is HOLD, `block_reason` (or "no candidate" when `block_reason == "no_majority"`), so a live run shows why a HOLD happened without needing to run the measurement script.

### 5. Documentation

After running `measure_fire_rate.py` against live data, update the HTF paragraph in `CLAUDE.md` with the newly measured fire rate and guard-block breakdown, if it differs meaningfully from the existing "0.5–2% of bars" note.

## Error handling

- A pair whose `fetch_time_series` call fails (all API keys exhausted) is skipped in the measurement script with a printed warning, same as the live loop already does.
- Bars before the warmup floor, or where any indicator is `None` due to insufficient history, are excluded from tallies (not counted as HOLD) — matches existing live-loop behavior of skipping evaluation when `last_rsi`/`ema_20`/`macd` is `None`.

## Testing / Verification

- Run `measure_fire_rate.py` with no args (all pairs) against live Twelve Data history and confirm output is well-formed and rates are in a plausible range (roughly consistent with the previously-measured 0.5–2% overall fire rate, now attributed across guards).
- Manually run `python main.py` briefly for one pair and confirm the new `htf_status`/`block_reason` fields appear correctly in the console line.
- No unit test suite exists in this repo (per `CLAUDE.md`); verification is empirical/manual, consistent with existing project practice.

## Out of scope

- Changing any indicator threshold, vote count, or guard margin based on what the measurement reveals — this is a measurement tool, not a tuning pass. Per `CLAUDE.md`, reactive threshold tuning has already been tried extensively without a real edge emerging; any follow-up tuning decision is separate and should be raised explicitly.
- Backtesting against actual trade outcomes (win rate, P/L) — this only measures fire rate and guard attribution, not profitability. `CLAUDE.md` recommends real backtesting infrastructure as a separate, larger effort.
- Splitting `main.py` into multiple modules beyond the one new top-level script — `evaluate_bar` stays in `main.py` per the project's existing single-file style.
