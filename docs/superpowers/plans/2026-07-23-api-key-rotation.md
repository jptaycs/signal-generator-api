# API Key Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rotate `main.py`'s Twelve Data API requests across the existing pool of 9 API keys — switching every 10 minutes on a schedule, and immediately on any request failure (rate limit, invalid/unsubscribed key) — so no single free-tier account's limit is exceeded and a dead key doesn't stall polling.

**Architecture:** Replace the single hardcoded `API_KEY` in `main.py` with an `API_KEYS` list of `{label, key}` dicts (from the existing commented-out keys), plus module-level rotation state and two small functions (`get_active_key`, `advance_key`). A new `fetch_time_series(symbol)` function wraps the Twelve Data request with retry-on-failure across the key pool, replacing the inline `requests.get(...)` call in the main loop.

**Tech Stack:** Python 3, `requests` (already a dependency). No new dependencies.

## Global Constraints

- Everything stays in `main.py` — this repo's convention is a single-file app; do not split into modules (see CLAUDE.md "Style").
- Do not change indicator logic, signal thresholds, or the Telegram message format in `send_trade_signal()` — the downstream `autobot2-auto-calibration-tweb` scraper depends on that wording (see CLAUDE.md "Key Details").
- Keep all 9 keys, including the dead "Unli" key — do not prune it (spec: keep-in-pool, failover handles it).
- No new dependencies — `requests`, `time`, `datetime` are already imported in `main.py`.
- This repo has no test suite or pytest — verification uses throwaway `assert`-based scripts run directly with `python3`, written to the scratchpad directory, never committed.
- `main.py` runs an interactive `input()` prompt at module level (not inside `if __name__ == "__main__":`), so any verification script that imports `main` must patch `builtins.input` first to avoid blocking.

---

### Task 1: API key pool and rotation state

**Files:**
- Modify: `main.py:8-17` (replace `API_KEY = ...` and the 9 commented key lines)

**Interfaces:**
- Produces: `API_KEYS` (list of `{"label": str, "key": str}`), `ROTATION_INTERVAL_SECONDS` (int, 600), `get_active_key() -> dict`, `advance_key(reason: str) -> None`. Later tasks call `get_active_key()` to obtain the currently active `{"label", "key"}` dict, and `advance_key(reason)` to force an immediate switch.

- [ ] **Step 1: Write the failing verification script**

Create `/private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task1.py`:

```python
import sys
import time
from unittest.mock import patch

sys.path.insert(0, "/Users/jptaycs/Documents/GitHub/signal-generator-api")

with patch("builtins.input", return_value="0"):
    import main

# Pool has all 9 keys, starts on the first one (Unli)
assert len(main.API_KEYS) == 9, f"expected 9 keys, got {len(main.API_KEYS)}"
assert main.API_KEYS[0]["label"] == "Unli"
first = main.get_active_key()
assert first["label"] == "Unli", f"expected Unli first, got {first['label']}"

# No rotation yet (just started)
again = main.get_active_key()
assert again["label"] == "Unli", "should not rotate before 10 minutes elapse"

# Force scheduled rotation by backdating key_started_at
main.key_started_at = time.time() - main.ROTATION_INTERVAL_SECONDS - 1
rotated = main.get_active_key()
assert rotated["label"] == "jptayco1109", f"expected scheduled rotation to jptayco1109, got {rotated['label']}"

# advance_key forces an immediate switch regardless of timer
main.advance_key(reason="test failure")
after_advance = main.get_active_key()
assert after_advance["label"] == "jptayco 2002", f"expected advance_key to move to jptayco 2002, got {after_advance['label']}"

# Wrap-around: force index to the last key, then advance
main.current_key_index = len(main.API_KEYS) - 1
main.advance_key(reason="test wraparound")
wrapped = main.get_active_key()
assert wrapped["label"] == "Unli", f"expected wraparound to Unli, got {wrapped['label']}"

print("Task 1 verification PASSED")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python3 /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task1.py`
Expected: `AttributeError: module 'main' has no attribute 'API_KEYS'` (since `API_KEYS` doesn't exist yet).

- [ ] **Step 3: Implement the key pool and rotation functions**

In `main.py`, replace lines 8-17 (the `API_KEY = ...` line and the 9 `# label - key` comment lines) with:

```python
API_KEYS = [
    {"label": "Unli", "key": "e0c7cd3a05a448bda0c737c99cc4790f"},
    {"label": "jptayco1109", "key": "652c4b836e0a44a8bb6c5b5004c7057c"},
    {"label": "jptayco 2002", "key": "67a1d34cee5c4fe6a3bac7d5bc1bf864"},
    {"label": "appnado", "key": "cf4fae9291334c638b4e71dc125a0863"},
    {"label": "sweet", "key": "62a2531773df4b6aa408b234041256d9"},
    {"label": "sweetMain", "key": "6debb834d8274930911045d03bf65673"},
    {"label": "jp icloud", "key": "2423e681b7314168a007bf8eb172f061"},
    {"label": "cath", "key": "41ab602809474f36985aadb6b849066e"},
    {"label": "sweetgbox", "key": "2988c838410642dbb950b62ad7505813"},
]

ROTATION_INTERVAL_SECONDS = 600  # 10 minutes

current_key_index = 0
key_started_at = time.time()


def get_active_key():
    global current_key_index, key_started_at
    if time.time() - key_started_at >= ROTATION_INTERVAL_SECONDS:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        key_started_at = time.time()
        print(f"[key-rotation] Scheduled switch (10 min elapsed) -> now using '{API_KEYS[current_key_index]['label']}'")
    return API_KEYS[current_key_index]


def advance_key(reason):
    global current_key_index, key_started_at
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    key_started_at = time.time()
    print(f"[key-rotation] Switching key due to {reason} -> now using '{API_KEYS[current_key_index]['label']}'")
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `python3 /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task1.py`
Expected: `Task 1 verification PASSED` printed, no assertion errors. (Scheduled-rotation and failover log lines will also print — that's expected.)

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
Replace single API key with rotating key pool

Adds the 9 existing Twelve Data keys (previously just comments) as a
rotation pool with scheduled 10-minute switching and a manual
advance_key() for failover, laying the groundwork for retrying failed
requests on a different account.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Request wrapper with failover

**Files:**
- Modify: `main.py` (add a new function after the Task 1 rotation code, before `send_trade_signal`)

**Interfaces:**
- Consumes: `API_KEYS`, `get_active_key()`, `advance_key(reason)` from Task 1.
- Produces: `fetch_time_series(symbol: str) -> dict | None`. Task 3 calls this instead of building the Twelve Data URL and calling `requests.get` inline. Returns the parsed JSON response dict on success (containing `"values"`), or `None` if every key in the pool failed for this symbol on this call.

- [ ] **Step 1: Write the failing verification script**

Create `/private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task2.py`:

```python
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/Users/jptaycs/Documents/GitHub/signal-generator-api")

with patch("builtins.input", return_value="0"):
    import main

# Reset rotation state so this test is deterministic regardless of Task 1's script having run
main.current_key_index = 0
import time as _time
main.key_started_at = _time.time()

# Case 1: first key rate-limited (429), second key succeeds
error_response = MagicMock()
error_response.status_code = 429
error_response.json.return_value = {"status": "error", "code": 429, "message": "You have run out of API credits"}
error_response.text = '{"status": "error"}'

success_response = MagicMock()
success_response.status_code = 200
success_response.json.return_value = {"status": "ok", "values": [{"datetime": "2026-07-23 09:30:00", "close": "1.2345"}]}

with patch("main.requests.get", side_effect=[error_response, success_response]):
    result = main.fetch_time_series("EUR/USD")

assert result is not None, "expected a successful result on the second key"
assert "values" in result
assert main.API_KEYS[main.current_key_index]["label"] == "jptayco1109", (
    f"expected failover to move to jptayco1109, got {main.API_KEYS[main.current_key_index]['label']}"
)

# Case 2: every key fails -> returns None, all keys attempted
main.current_key_index = 0
main.key_started_at = _time.time()

with patch("main.requests.get", return_value=error_response) as mock_get:
    result = main.fetch_time_series("GBP/USD")

assert result is None, "expected None when every key fails"
assert mock_get.call_count == len(main.API_KEYS), (
    f"expected one attempt per key ({len(main.API_KEYS)}), got {mock_get.call_count}"
)

print("Task 2 verification PASSED")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python3 /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task2.py`
Expected: `AttributeError: module 'main' has no attribute 'fetch_time_series'`

- [ ] **Step 3: Implement `fetch_time_series`**

In `main.py`, add this function after the `advance_key` function from Task 1 (and before `send_trade_signal`):

```python
def fetch_time_series(symbol):
    for attempt in range(len(API_KEYS)):
        key_info = get_active_key()
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval=1min&outputsize=1000&dp=2"
            f"&timezone=America/New_York&format=JSON"
        )
        response = requests.get(url)
        try:
            raw = response.json()
        except ValueError:
            raw = {}

        if response.status_code == 200 and raw.get("status") != "error" and "values" in raw:
            return raw

        error_msg = raw.get("message", response.text[:200])
        print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {error_msg} (HTTP {response.status_code})")
        advance_key(reason=f"error on {symbol}")

    print(f"[key-rotation] All API keys exhausted for {symbol}, skipping this cycle.")
    return None
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `python3 /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task2.py`
Expected: `Task 2 verification PASSED` printed, no assertion errors.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
Add fetch_time_series wrapper with key failover

Wraps the Twelve Data time_series request with automatic retry across
the key pool: a rate-limited or unauthorized response immediately
advances to the next key instead of stalling the whole polling cycle.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire into the main loop

**Files:**
- Modify: `main.py` (inside the `while True:` loop in the `if __name__ == "__main__":` block)

**Interfaces:**
- Consumes: `fetch_time_series(symbol)` from Task 2.

- [ ] **Step 1: Replace the inline request block**

In `main.py`, inside the `for symbol in list(pairs):` loop, replace:

```python
                url = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&interval=1min&outputsize=1000&dp=2&timezone=America/New_York&format=JSON"
                response = requests.get(url)
                raw = response.json()
                if "values" not in raw:
                    continue
```

with:

```python
                raw = fetch_time_series(symbol)
                if raw is None:
                    continue
```

- [ ] **Step 2: Confirm no leftover references to the old single key**

Run: `grep -n "API_KEY\b" main.py`
Expected: no output (the old singular `API_KEY` variable and all its usages are gone — only `API_KEYS` remains, which won't match this exact-word grep).

- [ ] **Step 3: Manual smoke test**

This repo has no test suite (per CLAUDE.md); the main loop itself is verified by running it briefly against the real API, since it performs live network calls and reads from `input()`.

Run: `python3 main.py`, then when prompted `What pair do you want to track? (0 for All, 1-17):`, type `1` and press Enter (tracks a single pair for a fast check).

Expected within a few seconds:
- A line like `AUD/CAD | Price: ... | RSI: ... | Signal: ...` prints (same format as before this change).
- If any key fails, a `[key-rotation] '<label>' failed for AUD/CAD: ...` line prints followed by a switch to the next key, and the price/signal line still ultimately appears (unless *all* 9 keys are exhausted, in which case a `[key-rotation] All API keys exhausted for AUD/CAD, skipping this cycle.` line prints instead and no price line appears that cycle — acceptable, it should recover next minute).

Press `Ctrl+C` to stop once you've confirmed one successful cycle. Expected: `Exiting...` prints and the process ends cleanly.

- [ ] **Step 4: Clean up scratch verification scripts**

Run: `rm -f /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task1.py /private/tmp/claude-501/-Users-jptaycs-Documents-GitHub-signal-generator-api/f1ba50e3-e297-4410-aa31-9ec609c02a6d/scratchpad/verify_task2.py`

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
Use rotating key pool in main polling loop

Replaces the inline single-key Twelve Data request with
fetch_time_series(), so the live polling loop benefits from scheduled
rotation and failover across all 9 keys.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
