# Stricter Signal Voting Design

## Problem

Signals were firing on weak or noisy consensus. The 7-indicator majority vote (4-of-7 to fire) used deliberately loose per-indicator thresholds, and MACD in particular has no HOLD zone — it always votes BUY or SELL based on the sign of a value that oscillates around zero on 1-minute forex data, making it a near-constant noise contributor rather than an independent signal.

## Goal

Reduce false/premature signals without a full architecture change: tighten the loosest individual thresholds, and add a trend-confirmation requirement so a majority vote must be corroborated by the two trend indicators (MACD, ADX) before it fires.

## Design

### Tightened individual thresholds

In the `if last_rsi is None or ema_20 is None or macd is None: ... else:` block of the main loop (`main.py`):

- **RSI**: `BUY` if `< 40` (was `< 48`), `SELL` if `> 60` (was `> 52`).
- **EMA20**: `BUY` if `price > ema_20 * 1.0005` (was `* 1.0001`), `SELL` if `price < ema_20 * 0.9995` (was `* 0.9999`).
- **MACD**: add a deadband scaled to price instead of a bare sign check. `BUY` if `macd > price * 0.00005`, `SELL` if `macd < -price * 0.00005`, else `HOLD`. (Previously: `BUY` if `macd >= 0` else `SELL`, no HOLD possible.)
- **CCI**: `BUY` if `< -100` (was `< -70`), `SELL` if `> 100` (was `> 70`).
- **ADX trend floor**: `15` → `20` in the existing `last_adx > 15` conditions (both BUY and SELL branches).

Stochastic (`35`/`65`) and Bollinger Bands (`0.0002`/`0.0002` margins) are unchanged — already reasonably tight.

### Trend-confirmation guard

After the existing 4-of-7 majority-vote check determines a candidate `BUY`/`SELL`, require both `macd_status` and `adx_status` to equal that same direction before the signal is allowed to stand; otherwise downgrade to `HOLD`. The vote threshold itself (4-of-7) is unchanged — this is an additional gate on top, not a higher count.

Concretely, after:
```python
if buy_count >= 4:
    signal = f"BUY (score={buy_count})"
elif sell_count >= 4:
    signal = f"SELL (score={sell_count})"
else:
    signal = f"HOLD (score={hold_count})"
```
add a confirmation check: a candidate `BUY` requires `macd_status == "BUY" and adx_status == "BUY"`; a candidate `SELL` requires `macd_status == "SELL" and adx_status == "SELL"`. If the candidate direction lacks that confirmation, the signal becomes `HOLD` instead (the score in the printed breakdown still reflects the raw vote count, so the log line stays informative about why a signal did/didn't fire).

### Documentation update

`CLAUDE.md` currently states: "intentionally loose thresholds (e.g. RSI 48/52 instead of the classic 30/70) — this is by design, not a bug." This is no longer accurate after this change and will be updated to describe the tightened thresholds and the trend-confirmation guard.

## Out of scope

- Raising the 4-of-7 vote threshold itself (user chose not to; the confirmation guard is the chosen additional gate instead).
- Changes to Stochastic or Bollinger Band thresholds.
- Changes to the Telegram message format, indicator set, or polling/rotation logic.
