# API Key Rotation Design

## Problem

`main.py` polls the Twelve Data API for 1-minute candle data on every tracked pair, once per minute. It currently uses a single hardcoded API key. Twelve Data's free tier has strict rate limits, and the currently-configured key ("Unli") is no longer subscribed, so requests using it fail. Nine keys from different free-tier accounts already exist as comments in the file (lines 9-17) but aren't used programmatically.

## Goal

Rotate across the existing pool of API keys so free-tier limits on any single account are less likely to be exceeded, and so a dead/unsubscribed/rate-limited key doesn't stall the app.

## Design

### Key pool

`API_KEYS`: a list of `{"label": ..., "key": ...}` dicts built from the 9 keys currently listed as comments in `main.py` (including the dead "Unli" key — no manual pruning needed, see Failover below).

### Rotation state

Module-level globals:
- `current_index`: index into `API_KEYS` of the currently active key.
- `key_started_at`: timestamp when the current key became active.

### Scheduled rotation

`get_active_key()`:
- If `time.time() - key_started_at >= 600` (10 minutes), advance `current_index` to the next key (wrapping around with modulo) and reset `key_started_at`.
- Returns the active key dict.

This provides time-based rotation every 10 minutes regardless of errors, spreading request volume across accounts.

### Failover on error

`advance_key(reason)`:
- Immediately advances `current_index` to the next key (wrapping) and resets `key_started_at`.
- Logs the reason (e.g. which symbol/error triggered it) and the new active key's label.

### Request wrapper

`fetch_time_series(symbol)` replaces the current inline `requests.get(...)` call in the main loop:
1. Loop up to `len(API_KEYS)` attempts.
2. Call `get_active_key()` to get the current key (applying scheduled rotation if due).
3. Build the Twelve Data URL with that key and request it.
4. Success condition: HTTP 200, response JSON does not have `"status": "error"`, and `"values"` is present in the response.
5. On success: return the parsed JSON.
6. On failure: log the key's label, the symbol, and the error message (from the response JSON's `message` field, or raw response text if unparseable), then call `advance_key()` and retry with the next key.
7. If all keys in the pool fail within one call, log that all keys are exhausted for this symbol and return `None`.

### Main loop integration

The existing inline block:
```python
url = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&..."
response = requests.get(url)
raw = response.json()
if "values" not in raw:
    continue
```
is replaced with:
```python
raw = fetch_time_series(symbol)
if raw is None:
    continue
```

All downstream indicator/signal logic is unchanged.

### Logging

Print statements (matching the existing print-based logging style) note:
- Scheduled rotations: which key is now active and that it was a scheduled 10-minute switch.
- Failover rotations: which key failed, the symbol, the error message, and which key is now active.
- Exhaustion: when all keys fail for a symbol in one cycle.

## Out of scope

- Moving keys to environment variables / `.env` (user explicitly chose to keep keys hardcoded in `main.py` for now).
- Removing the dead "Unli" key from the pool (kept in; failover skips it automatically).
- Any change to indicator logic, signal thresholds, or the Telegram message format.
