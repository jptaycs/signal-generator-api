import os
import requests
from datetime import datetime
import time
import pandas as pd
import ta
from dotenv import load_dotenv

load_dotenv()


# Pool of Twelve Data API keys, rotated across to stay under each free-tier account's rate
# limit. Loaded from the environment (TWELVEDATA_API_KEY_1, _2, ... and matching _LABEL
# vars) rather than hardcoded, since this repo is public — see .env.example for the format.
def _load_api_keys():
    keys = []
    i = 1
    while True:
        key = os.environ.get(f"TWELVEDATA_API_KEY_{i}")
        if not key:
            break
        label = os.environ.get(f"TWELVEDATA_API_KEY_{i}_LABEL", f"key{i}")
        keys.append({"label": label, "key": key})
        i += 1
    if not keys:
        raise RuntimeError(
            "No TWELVEDATA_API_KEY_1 (or higher) found in the environment. "
            "Copy .env.example to .env and fill in real values."
        )
    return keys


API_KEYS = _load_api_keys()

ROTATION_INTERVAL_SECONDS = 600  # 10 minutes

current_key_index = 0
key_started_at = time.time()


# --- API key rotation ---
# Returns the currently active key, scheduling a rotation to the next key
# every ROTATION_INTERVAL_SECONDS regardless of whether requests are failing.
def get_active_key():
    global current_key_index, key_started_at
    if time.time() - key_started_at >= ROTATION_INTERVAL_SECONDS:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        key_started_at = time.time()
        print(f"[key-rotation] Scheduled switch (10 min elapsed) -> now using '{API_KEYS[current_key_index]['label']}'")
    return API_KEYS[current_key_index]


# Immediately moves to the next key (failover), bypassing the 10-minute
# schedule — used when the active key just failed a request.
def advance_key(reason):
    global current_key_index, key_started_at
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    key_started_at = time.time()
    print(f"[key-rotation] Switching key due to {reason} -> now using '{API_KEYS[current_key_index]['label']}'")


# Fetches 1-minute candles for `symbol`, retrying with the next key (via
# advance_key) on any failure — HTTP error, API error response, malformed
# JSON, or network exception — up to once per key in the pool.
def fetch_time_series(symbol):
    for _ in range(len(API_KEYS)):
        # If the 10-min rotation timer fires here, this call may skip the key that was
        # active on entry (it advances once via the timer, again on failure) — harmless,
        # it gets picked up again on a later cycle.
        key_info = get_active_key()
        # dp=5 for full forex pip/pipette precision (0.0001 pip + a 5th decimal) — dp=2
        # rounded most non-JPY pairs (e.g. EUR/USD ~1.14xxx) to a flat, unmoving price,
        # which silently starved every indicator of real price variance.
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval=1min&outputsize=1000&dp=5"
            f"&timezone=America/New_York&format=JSON"
        )
        try:
            response = requests.get(url)
        except requests.exceptions.RequestException as exc:
            error_msg = str(exc)
            print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {error_msg}")
            advance_key(reason=f"error on {symbol}")
            continue

        try:
            raw = response.json()
        except ValueError:
            raw = {}

        # Only treat it as success if the HTTP call succeeded, Twelve Data
        # didn't return an API-level error, and candle data is actually present.
        if response.status_code == 200 and raw.get("status") != "error" and "values" in raw:
            return raw

        error_msg = raw.get("message", response.text[:200])
        print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {error_msg} (HTTP {response.status_code})")
        advance_key(reason=f"error on {symbol}")

    # Every key in the pool failed for this symbol this cycle; caller skips it
    # and will retry on the next poll.
    print(f"[key-rotation] All API keys exhausted for {symbol}, skipping this cycle.")
    return None

bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if not bot_token or not chat_id:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set in the environment. "
        "Copy .env.example to .env and fill in real values."
    )
def send_trade_signal(symbol, action, expiration_minutes):
    current_time = datetime.now().strftime("%H:%M")
    emoji = "🟩" if action.upper() == "BUY" else "🟥"
    message = (
        f"{symbol}\n"
        # f"🕘 Expiration {expiration_minutes}M\n"
        f"⏺ Entry at {current_time}\n"
        f"{emoji} {action.upper()}"
    )
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    response = requests.post(url, data=payload)
    if response.status_code != 200:
        print("Failed to send Telegram message:", response.text)


QUEUE_FILE = "queue.md"


def _fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_duration(seconds):
    sign = "-" if seconds < 0 else ""
    minutes, secs = divmod(int(abs(seconds)), 60)
    return f"{sign}{minutes}m {secs}s"


# Writes queue.md, a live snapshot of the 30-min delayed-send queue (pending
# and recently-sent), so the delay mechanism can be observed without reading
# process stdout. Rewritten on every queue change — not a persisted append
# log, just current in-memory state; restarting the process resets it.
def write_queue_snapshot(pending_signals, sent_history):
    now_ts = time.time()
    lines = [
        "# Signal Delay Queue",
        "",
        f"_Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "## Pending (queued, will send after 30 min)",
        "",
    ]
    if pending_signals:
        lines.append("| Symbol | Action | Queued At | Send At (ETA) | Time Remaining |")
        lines.append("|---|---|---|---|---|")
        for p in pending_signals:
            lines.append(
                f"| {p['symbol']} | {p['action']} | {_fmt_time(p['queued_at'])} | "
                f"{_fmt_time(p['send_at'])} | {_fmt_duration(p['send_at'] - now_ts)} |"
            )
    else:
        lines.append("_(none pending)_")

    lines += [
        "",
        "## Recently Sent (delayed-send history, most recent first, last 50)",
        "",
    ]
    if sent_history:
        lines.append("| Symbol | Action | Queued At | Sent At | Actual Delay |")
        lines.append("|---|---|---|---|---|")
        for s in reversed(sent_history[-50:]):
            lines.append(
                f"| {s['symbol']} | {s['action']} | {_fmt_time(s['queued_at'])} | "
                f"{_fmt_time(s['sent_at'])} | {_fmt_duration(s['sent_at'] - s['queued_at'])} |"
            )
    else:
        lines.append("_(none sent yet)_")

    with open(QUEUE_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


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

    if buy_count >= 5:
        candidate_signal = "BUY"
    elif sell_count >= 5:
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


# Reduced from the full 22-pair list to cut per-cycle API polling load (each
# tracked pair costs a fetch_time_series() call every 60s, and free-tier
# credits are the binding constraint — see CLAUDE.md's "Free-Tier API
# Limitations"). Kept: the 6 forex majors (most liquid, most stable price
# action) plus EUR/JPY and GBP/JPY (liquid JPY crosses). Commented out below
# rather than deleted, so any of them can be re-enabled by uncommenting.
pairs = [
    # "AUD/CAD",
    # "AUD/CHF",
    # "AUD/JPY",
    "AUD/USD",
    # "CAD/JPY",
    # "CAD/CHF",  # measured 0.00% fired across ~940 bars (measure_fire_rate.py)
    # "CHF/JPY",
    # "EUR/AUD",
    # "EUR/CAD",
    # "EUR/CHF",
    # "EUR/GBP",
    "EUR/JPY",
    "EUR/USD",
    # "GBP/AUD",
    # "GBP/CAD",
    # "GBP/CHF",  # measured 0.00% fired across ~940 bars (measure_fire_rate.py)
    "GBP/JPY",
    "GBP/USD",
    # "NZD/JPY",
    "USD/CAD",
    "USD/CHF",
    "USD/JPY",
]

if __name__ == "__main__":
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

    # Delayed-send queue: a signal is queued here the moment it fires, then sent
    # unchanged (no re-check) SIGNAL_DELAY_SECONDS later. Non-blocking — a plain
    # time.sleep() here would stall polling of every other pair for the whole
    # delay, so instead each poll cycle just flushes whatever in the queue has
    # matured. Based on live observation: a 5-minute expiration lost consistently,
    # while a manually-tested 1-hour expiration won — the signal direction wasn't
    # wrong, it just needed more time than 5 minutes to play out. This delay +
    # the 30-minute expiration below are a first attempt at matching that, not
    # empirically re-tuned yet.
    SIGNAL_DELAY_SECONDS = 30 * 60
    pending_signals = []
    sent_history = []
    write_queue_snapshot(pending_signals, sent_history)

    try:
        while True:
            # Flush any delayed signals whose wait has elapsed, oldest first.
            now_ts = time.time()
            still_pending = []
            queue_changed = False
            for pending in pending_signals:
                if now_ts >= pending["send_at"]:
                    send_trade_signal(pending["symbol"], pending["action"], pending["expiration_minutes"])
                    sent_history.append({
                        "symbol": pending["symbol"],
                        "action": pending["action"],
                        "queued_at": pending["queued_at"],
                        "sent_at": now_ts,
                    })
                    queue_changed = True
                else:
                    still_pending.append(pending)
            pending_signals = still_pending
            if queue_changed:
                write_queue_snapshot(pending_signals, sent_history)

            for symbol in list(pairs):
                raw = fetch_time_series(symbol)
                if raw is None:
                    continue
                df = pd.DataFrame(raw["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime")
                # Twelve Data returns real per-candle open/high/low/close — use the actual
                # high/low (not a close-only stand-in) so range-based indicators (Stochastic,
                # CCI, ADX) get real intrabar data instead of a collapsed high=low=close series.
                df["high"] = df["high"].astype(float)
                df["low"] = df["low"].astype(float)
                df["close"] = df["close"].astype(float)

                result = evaluate_bar(df)
                price = result["price"]
                signal = result["signal"]

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

                if signal.startswith("BUY") or signal.startswith("SELL"):
                    expiration_minutes = 30
                    action = signal.split()[0]
                    send_at = now_ts + SIGNAL_DELAY_SECONDS
                    pending_signals.append({
                        "symbol": symbol,
                        "action": action,
                        "expiration_minutes": expiration_minutes,
                        "queued_at": now_ts,
                        "send_at": send_at,
                    })
                    write_queue_snapshot(pending_signals, sent_history)
                    print(
                        f"[delayed-send] Queued {action} for {symbol}, "
                        f"will send at {datetime.fromtimestamp(send_at).strftime('%H:%M:%S')}"
                    )

            # Wait until the start of the next minute
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            time.sleep(seconds_to_wait)
    except KeyboardInterrupt:
        print("Exiting...")