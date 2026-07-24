import os
import requests
from datetime import datetime
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
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


def get_active_key():
    """Returns the currently active key, scheduling a rotation to the next key
    every ROTATION_INTERVAL_SECONDS regardless of whether requests are failing."""
    global current_key_index, key_started_at
    if time.time() - key_started_at >= ROTATION_INTERVAL_SECONDS:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        key_started_at = time.time()
        print(f"[key-rotation] Scheduled switch (10 min elapsed) -> now using '{API_KEYS[current_key_index]['label']}'")
    return API_KEYS[current_key_index]


def advance_key(reason):
    """Immediately moves to the next key (failover), bypassing the 10-minute
    schedule — used when the active key just failed a request."""
    global current_key_index, key_started_at
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    key_started_at = time.time()
    print(f"[key-rotation] Switching key due to {reason} -> now using '{API_KEYS[current_key_index]['label']}'")


def fetch_time_series(symbol):
    """Tries each API key in the rotation pool once for this symbol, failing over
    immediately on any error. Replaces the old fixed-key 5x retry loop."""
    for _ in range(len(API_KEYS)):
        key_info = get_active_key()
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval=1h&outputsize=1000&dp=2"
            f"&timezone=America/New_York&format=JSON"
        )
        try:
            response = requests.get(url, timeout=10)
        except requests.exceptions.RequestException as exc:
            print(f"⚠️ '{key_info['label']}' failed for {symbol}: {exc}")
            advance_key(reason=f"error on {symbol}")
            continue

        try:
            raw = response.json()
        except ValueError:
            raw = {}

        if response.status_code == 200 and raw.get("status") != "error" and "values" in raw:
            return raw

        error_msg = raw.get("message", response.text[:200])
        print(f"⚠️ '{key_info['label']}' failed for {symbol}: {error_msg} (HTTP {response.status_code})")
        advance_key(reason=f"error on {symbol}")

    print(f"❌ All API keys exhausted for {symbol}, skipping this cycle.")
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
        start_time = datetime.now()
        while True:
            now = datetime.now()
            elapsed = (now - start_time).total_seconds()
            if elapsed < 20 * 60:
                for symbol in list(pairs):
                    raw = fetch_time_series(symbol)
                    if raw is None or "values" not in raw:
                        continue
                    df = pd.DataFrame(raw["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime")
                    df["close"] = df["close"].astype(float)
                    price = df["close"].iloc[-1]

                    rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
                    rsi_values = rsi_indicator.rsi()
                    last_rsi = rsi_values.iloc[-1] if len(rsi_values) > 0 else None

                    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None

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
                        # Determine status for each indicator (even looser thresholds)
                        rsi_status = "BUY" if last_rsi < 48 else "SELL" if last_rsi > 52 else "HOLD"
                        ema_status = "BUY" if price > ema_20 * 1.0001 else "SELL" if price < ema_20 * 0.9999 else "HOLD"
                        macd_status = "BUY" if macd >= 0 else "SELL"
                        stoch_status = "BUY" if stoch_k is not None and stoch_k < 35 else "SELL" if stoch_k is not None and stoch_k > 65 else "HOLD"
                        bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.0002 else "SELL" if bb_high is not None and price >= bb_high * 0.9998 else "HOLD"
                        cci_status = "BUY" if last_cci is not None and last_cci < -70 else "SELL" if last_cci is not None and last_cci > 70 else "HOLD"
                        adx_status = "BUY" if last_adx is not None and last_adx > 15 and macd > 0 else "SELL" if last_adx is not None and last_adx > 15 and macd < 0 else "HOLD"

                        statuses = [rsi_status, ema_status, macd_status, stoch_status, bb_status, cci_status, adx_status]
                        buy_count = statuses.count("BUY")
                        sell_count = statuses.count("SELL")
                        hold_count = statuses.count("HOLD")

                        if buy_count >= 4:
                            signal = f"BUY (score={buy_count})"
                        elif sell_count >= 4:
                            signal = f"SELL (score={sell_count})"
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

                # Inside the 20-minute active window, sleep 5 minutes before next check
                time.sleep(60)
            else:
                # After 20 minutes active, sleep until one hour from start_time
                next_cycle = start_time + pd.Timedelta(minutes=30)
                seconds_to_wait = (next_cycle - now).total_seconds()
                if seconds_to_wait > 0:
                    time.sleep(seconds_to_wait)
                start_time = datetime.now()
    except KeyboardInterrupt:
        print("Exiting...")