import requests
from datetime import datetime
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo

# Pool of Twelve Data API keys, rotated across to stay under each free-tier account's rate limit.
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

bot_token = "8119532010:AAHBTjlpUUgln260B1a2leDOu1oy6A2WnRo"
chat_id = "6460198665"  # Replace with your Telegram user ID or channel ID
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

pairs = [
    "AUD/CAD", 
    "AUD/CHF", 
    "AUD/JPY",
    "AUD/USD",
    "CAD/JPY",
    "CAD/CHF",
    "CHF/JPY", 
    "EUR/AUD", 
    "EUR/CAD",
    "EUR/CHF", 
    "EUR/GBP", 
    "EUR/JPY",
    "EUR/USD",
    "GBP/AUD",
    "GBP/CAD",
    "GBP/CHF", 
    "GBP/JPY", 
    "GBP/USD", 
    "NZD/JPY",
    "USD/CAD", 
    "USD/CHF", 
    "USD/JPY", 
]

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

if __name__ == "__main__":
    try:
        while True:
            for symbol in list(pairs):
                raw = fetch_time_series(symbol)
                if raw is None:
                    continue
                df = pd.DataFrame(raw["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime")
                df["close"] = df["close"].astype(float)
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
                    high=df["close"], low=df["close"], close=df["close"], window=14, smooth_window=3
                )
                stoch_k = stoch.stoch().iloc[-1] if len(df) > 0 else None

                # Bollinger Bands
                bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
                bb_high = bb.bollinger_hband().iloc[-1] if len(df) > 0 else None
                bb_low = bb.bollinger_lband().iloc[-1] if len(df) > 0 else None

                # Commodity Channel Index (CCI)
                cci = ta.trend.CCIIndicator(high=df["close"], low=df["close"], close=df["close"], window=20)
                last_cci = cci.cci().iloc[-1] if len(df) > 0 else None

                # Average Directional Index (ADX)
                adx = ta.trend.ADXIndicator(high=df["close"], low=df["close"], close=df["close"], window=14)
                last_adx = adx.adx().iloc[-1] if len(df) > 0 else None

                if last_rsi is None or ema_20 is None or macd is None:
                    signal = "HOLD"
                else:
                    # Each of the 7 indicators independently votes BUY/SELL/HOLD.
                    # Thresholds are moderately tightened from the original "loose" values —
                    # eased back a bit further after the 5-of-7 vote threshold (below) made
                    # the combination of both too strict to fire at all.
                    rsi_status = "BUY" if last_rsi < 45 else "SELL" if last_rsi > 55 else "HOLD"
                    ema_status = "BUY" if price > ema_20 * 1.0002 else "SELL" if price < ema_20 * 0.9998 else "HOLD"
                    # MACD deadband (scaled to price) instead of a bare sign check, so small
                    # oscillations around zero vote HOLD rather than always BUY/SELL.
                    macd_status = "BUY" if macd > price * 0.00002 else "SELL" if macd < -price * 0.00002 else "HOLD"
                    stoch_status = "BUY" if stoch_k is not None and stoch_k < 35 else "SELL" if stoch_k is not None and stoch_k > 65 else "HOLD"
                    bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.0002 else "SELL" if bb_high is not None and price >= bb_high * 0.9998 else "HOLD"
                    cci_status = "BUY" if last_cci is not None and last_cci < -75 else "SELL" if last_cci is not None and last_cci > 75 else "HOLD"
                    adx_status = "BUY" if last_adx is not None and last_adx > 16 and macd > 0 else "SELL" if last_adx is not None and last_adx > 16 and macd < 0 else "HOLD"

                    # Majority vote: a candidate signal needs at least 4 of 7 indicators to
                    # agree. (A prior 5-of-7 requirement was calibrated against data
                    # corrupted by an upstream dp=2 rounding bug — see fetch_time_series —
                    # that made most non-JPY pairs report an almost-flat price; on real,
                    # unrounded price data a 4-of-7 majority is common while 5-of-7 is rare.)
                    statuses = [rsi_status, ema_status, macd_status, stoch_status, bb_status, cci_status, adx_status]
                    buy_count = statuses.count("BUY")
                    sell_count = statuses.count("SELL")
                    hold_count = statuses.count("HOLD")

                    if buy_count >= 4:
                        candidate_signal = "BUY"
                    elif sell_count >= 4:
                        candidate_signal = "SELL"
                    else:
                        candidate_signal = None

                    # Trend-confirmation guard: a majority isn't enough on its own — at least
                    # one of MACD/ADX (the two trend indicators) must also agree with the
                    # candidate direction, or the signal is downgraded to HOLD. Requiring only
                    # one (not both) avoids blocking on ADX's trend-strength floor rarely being
                    # met on noisy 1-minute data, while still filtering out majorities with
                    # zero trend backing at all.
                    if candidate_signal is not None and (macd_status == candidate_signal or adx_status == candidate_signal):
                        signal = f"{candidate_signal} (score={buy_count if candidate_signal == 'BUY' else sell_count})"
                    else:
                        signal = f"HOLD (score={hold_count})"

                ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
                stoch_str = f"{stoch_k:.2f}" if stoch_k is not None else "N/A"
                bb_high_str = f"{bb_high:.5f}" if bb_high is not None else "N/A"
                bb_low_str = f"{bb_low:.5f}" if bb_low is not None else "N/A"
                cci_str = f"{last_cci:.2f}" if last_cci is not None else "N/A"
                adx_str = f"{last_adx:.2f}" if last_adx is not None else "N/A"

                print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | Stoch: {stoch_str} ({stoch_status}) | BB: Low {bb_low_str}, High {bb_high_str} ({bb_status}) | CCI: {cci_str} ({cci_status}) | ADX: {adx_str} ({adx_status}) | Signal: {signal} | Breakdown: BUY={buy_count}, SELL={sell_count}, HOLD={hold_count}")

                if signal.startswith("BUY") or signal.startswith("SELL"):
                    expiration_minutes = 5
                    expiration_time = f"{expiration_minutes} minutes"
                    trade_signal = {
                        "pair": symbol,
                        "action": signal,
                        "expiration": expiration_time,
                        "time": datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S %Z")
                    }
                    send_trade_signal(symbol, signal.split()[0], expiration_minutes)

            # Wait until the start of the next minute
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            time.sleep(seconds_to_wait)
    except KeyboardInterrupt:
        print("Exiting...")