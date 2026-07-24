import requests
from datetime import datetime, timedelta, timezone
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import random
import http.client
import json
import pytz
import os
from dotenv import load_dotenv

load_dotenv()

# Timezone for news and trading
NY_TZ = pytz.timezone("America/New_York")

# --- Manual or Auto Fundamental Data (Forecast vs Previous) ---
# You can update these daily or automatically in future versions
currency_fundamentals = {
    # "CHF": {"forecast": 5.22, "previous": 4.01},  # Trade Balance - better than expected
    # "GBP": {"forecast": -20.7, "previous": -17.7},  # Public Sector Borrowing - worse (more debt)
    # "CNY": {"forecast": -12.7, "previous": 0},  # FDI drop - weaker
    # "CAD": {"forecast": 3.0, "previous": 3.1},  # CPI slightly cooler
    # "NZD": {"forecast": -1.6, "previous": 0},  # GDT index down
    # "JPY": {"forecast": -0.11, "previous": -0.15},  # Trade balance improved
}



# Add your news schedule with time (NY timezone)
currency_news_schedule = {
    "CHF": [
        datetime.now(NY_TZ).replace(hour=2, minute=0, second=0, microsecond=0),  # Trade Balance
    ],
    "GBP": [
        datetime.now(NY_TZ).replace(hour=2, minute=0, second=0, microsecond=0),  # Borrowing
        datetime.now(NY_TZ).replace(hour=4, minute=0, second=0, microsecond=0),  # 30y Bond Auction
    ],
    "CNY": [
        datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0),  # FDI tentative
    ],
    "EUR": [
        datetime.now(NY_TZ).replace(hour=7, minute=0, second=0, microsecond=0),  # Lagarde
        datetime.now(NY_TZ).replace(hour=18, minute=0, second=0, microsecond=0), # Nagel
    ],
    "CAD": [
        datetime.now(NY_TZ).replace(hour=8, minute=30, second=0, microsecond=0), # CPI set
    ],
    "USD": [
        datetime.now(NY_TZ).replace(hour=9, minute=0, second=0, microsecond=0),  # Waller Speaks
        datetime.now(NY_TZ).replace(hour=15, minute=30, second=0, microsecond=0),# Waller again
        datetime.now(NY_TZ).replace(hour=16, minute=30, second=0, microsecond=0),# API Bulletin
    ],
    "NZD": [
        datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0),  # GDT tentative
    ],
    "JPY": [
        datetime.now(NY_TZ).replace(hour=19, minute=50, second=0, microsecond=0), # Trade Balance
    ],
}



def get_fundamental_bias(currency):
    """Returns BUY, SELL, or NEUTRAL based on forecast vs previous and news time."""
    now = datetime.now(NY_TZ)
    news_times = currency_news_schedule.get(currency, [])

    # Determine if any news is within ±1 hour
    in_window = False
    for news_time in news_times:
        if abs((now - news_time).total_seconds()) <= 3600:  # ±1 hour
            in_window = True
            break

    if not in_window:
        return "NEUTRAL"  # Outside ±1 hour window

    # Inside window, use forecast vs previous
    data = currency_fundamentals.get(currency, {})
    forecast = data.get("forecast")
    previous = data.get("previous")
    if forecast is None or previous is None:
        return "NEUTRAL"
    if forecast > previous:
        return "BUY"
    elif forecast < previous:
        return "SELL"
    return "NEUTRAL"

notified_events = set()
blocked_currencies = {}  # e.g., { "USD": datetime_until_unblocked }


def send_telegram_message(text, markdown=True):
    """Helper to send Telegram alerts."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try:
        response = requests.post(url, data=payload, timeout=10)
        print("Telegram response:", response.status_code, response.text)
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")


def show_news_status_summary():
    """Prints and sends Telegram summary of currently blocked currencies."""
    now = datetime.now(NY_TZ)
    if not blocked_currencies:
        summary = "✅ *All currencies are clear for trading.*"
        print(summary)
        send_telegram_message(summary)
        return

    summary_lines = ["🕒 *Active News Blocks:*"]
    for currency, until in blocked_currencies.items():
        remaining = (until - now).total_seconds() / 60
        if remaining > 0:
            summary_lines.append(f"• {currency}: blocked until {until.strftime('%H:%M')} NY ({remaining:.0f} min left)")
    summary = "\n".join(summary_lines)
    print(summary)
    send_telegram_message(summary)





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
    """Fetches 1-minute time series for a symbol with automatic key rotation and failover."""
    for _ in range(len(API_KEYS)):
        key_info = get_active_key()
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval=1min&outputsize=1000&dp=5"
            f"&timezone=America/New_York&format=JSON"
        )
        try:
            response = requests.get(url, timeout=10)
        except requests.exceptions.RequestException as exc:
            error_msg = str(exc)
            print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {error_msg}")
            advance_key(reason=f"error on {symbol}")
            continue

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


# Telegram credentials loaded from environment
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
        f"⏺ Entry at {current_time}\n"
        f"{emoji} {action.upper()}"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                print(f"✅ Telegram alert sent for {symbol}: {action}")
                return True
            else:
                print(f"⚠️ Telegram error ({response.status_code}): {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Telegram connection issue (attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(2 ** attempt + random.random())  # exponential backoff

    print(f"❌ Failed to send Telegram message for {symbol} after {max_retries} attempts.")
    return False


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
    "USD/CAD", 
    "USD/CHF", 
    "USD/JPY", 
]

print("Tracking all available pairs automatically.")

price_history = {pair: [] for pair in pairs}

if __name__ == "__main__":
    try:
        start_time = datetime.now()
        while True:
            now = datetime.now()
            elapsed = (now - start_time).total_seconds()
            if elapsed < 30 * 60:
                for symbol in list(pairs):
                    base_currency, quote_currency = symbol.split("/")
                    base_bias = get_fundamental_bias(base_currency)
                    quote_bias = get_fundamental_bias(quote_currency)
                    raw = fetch_time_series(symbol)
                    if raw is None:
                        continue
                    if "values" not in raw:
                        continue
                    df = pd.DataFrame(raw["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime")
                    df["close"] = df["close"].astype(float)
                    price = df["close"].iloc[-1]

                    # --- Calculate 8 technical indicators (2 per category) ---
                    # Trend: EMA20, MACD
                    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None
                    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
                    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
                    macd = (ema_12 - ema_26).iloc[-1] if len(df) >= 26 else None

                    # Momentum: RSI, Stochastic Oscillator %K
                    rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
                    rsi_values = rsi_indicator.rsi()
                    last_rsi = rsi_values.iloc[-1] if len(rsi_values) > 0 else None
                    stoch = ta.momentum.StochasticOscillator(
                        high=df["close"], low=df["close"], close=df["close"], window=14, smooth_window=3
                    )
                    stoch_k = stoch.stoch().iloc[-1] if len(df) > 0 else None

                    # Volatility: Bollinger Bands, ATR
                    bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
                    bb_high = bb.bollinger_hband().iloc[-1] if len(df) > 0 else None
                    bb_low = bb.bollinger_lband().iloc[-1] if len(df) > 0 else None
                    atr = ta.volatility.AverageTrueRange(high=df["close"], low=df["close"], close=df["close"], window=14)
                    last_atr = atr.average_true_range().iloc[-1] if len(df) > 0 else None

                    # Volume: MFI (Money Flow Index) as a proxy for OBV
                    try:
                        mfi = ta.volume.MFIIndicator(
                            high=df["close"], low=df["close"], close=df["close"],
                            volume=pd.Series(range(len(df))), window=14
                        )
                        last_mfi = mfi.money_flow_index().iloc[-1] if len(df) > 0 else None
                    except Exception:
                        last_mfi = None

                    if last_rsi is None or ema_20 is None or macd is None:
                        signal = "HOLD"
                        buy_score = sell_score = hold_score = 0
                    else:
                        # --- Thresholds for 8 indicators ---
                        # Trend
                        ema_status = "BUY" if price > ema_20 else "SELL" if price < ema_20 else "HOLD"
                        macd_status = "BUY" if macd > 0 else "SELL" if macd < 0 else "HOLD"
                        # Momentum
                        rsi_status = "BUY" if last_rsi < 50 else "SELL" if last_rsi > 40 else "HOLD"
                        stoch_status = "BUY" if stoch_k is not None and stoch_k < 45 else "SELL" if stoch_k is not None and stoch_k > 55 else "HOLD"
                        # Volatility
                        bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.005 else "SELL" if bb_high is not None and price >= bb_high * 0.995 else "HOLD"
                        # ATR: compare to short-term average ATR to detect abnormally high volatility
                        atr_series = atr.average_true_range() if hasattr(atr, 'average_true_range') else None
                        avg_atr = None
                        if atr_series is not None and len(atr_series) >= 5:
                            avg_atr = atr_series.iloc[-5:].mean()
                        atr_status = "HOLD"
                        if last_atr is not None and avg_atr is not None:
                            atr_status = "BUY" if last_atr > avg_atr * 1.02 else "HOLD"
                        # Volume
                        if last_mfi is not None:
                            mfi_status = "BUY" if last_mfi < 40 else "SELL" if last_mfi > 60 else "HOLD"
                        else:
                            mfi_status = "HOLD"

                        # --- Indicator weights (simplified) ---
                        indicator_weights = {
                            # "ema20": 1,      # Trend
                            "macd": 1,       # Trend
                            # "rsi": 1,        # Momentum
                            "stoch_k": 1,    # Momentum
                            "bb": 1,         # Volatility
                            # "atr": 1,        # Volatility
                            "mfi": 1,        # Volume
                        }

                        indicator_statuses = {
                            # "ema20": ema_status,
                            "macd": macd_status,
                            # "rsi": rsi_status,
                            "stoch_k": stoch_status,
                            "bb": bb_status,
                            # "atr": atr_status,
                            "mfi": mfi_status,
                        }

                        buy_score = 0
                        sell_score = 0
                        hold_score = 0
                        for ind, status in indicator_statuses.items():
                            w = indicator_weights[ind]
                            if status == "BUY":
                                buy_score += w
                            elif status == "SELL":
                                sell_score += w
                            else:
                                hold_score += w

                        # --- Multi-timeframe confirmation (optional, can keep or remove) ---
                        # For simplicity, remove higher timeframe logic here

                        # Final signal logic for 8 indicators
                        if buy_score >= 4 and sell_score <= 1:
                            signal = "BUY"
                        elif sell_score >= 4 and buy_score <= 1:
                            signal = "SELL"
                        else:
                            signal = "HOLD"

                    ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                    macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                    rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
                    stoch_str = f"{stoch_k:.2f}" if stoch_k is not None else "N/A"
                    bb_high_str = f"{bb_high:.5f}" if bb_high is not None else "N/A"
                    bb_low_str = f"{bb_low:.5f}" if bb_low is not None else "N/A"
                    atr_str = f"{last_atr:.5f}" if last_atr is not None else "N/A"
                    mfi_str = f"{last_mfi:.2f}" if last_mfi is not None else "N/A"

                    print(f"{symbol} | Price: {price:.5f} | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | RSI: {rsi_str} ({rsi_status}) | Stoch: {stoch_str} ({stoch_status}) | BB: Low {bb_low_str}, High {bb_high_str} ({bb_status}) | ATR: {atr_str} ({atr_status}) | MFI: {mfi_str} ({mfi_status}) | Signal: {signal} | Weighted: BUY={buy_score}, SELL={sell_score}, HOLD={hold_score} | Fundamental Bias: {base_currency}={base_bias}, {quote_currency}={quote_bias}")

                    # --- Fundamental Bias Blocking ---
                    if signal.startswith("BUY") and (base_bias == "SELL" or quote_bias == "BUY"):
                        print(f"🚫 Blocked BUY signal for {symbol} due to fundamental bias ({base_currency}: {base_bias}, {quote_currency}: {quote_bias})")
                        continue
                    elif signal.startswith("SELL") and (base_bias == "BUY" or quote_bias == "SELL"):
                        print(f"🚫 Blocked SELL signal for {symbol} due to fundamental bias ({base_currency}: {base_bias}, {quote_currency}: {quote_bias})")
                        continue

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
                time.sleep(61)
            else:
                # After 20 minutes active, sleep until one hour from start_time
                next_cycle = start_time + pd.Timedelta(minutes=30)
                seconds_to_wait = (next_cycle - now).total_seconds()
                if seconds_to_wait > 0:
                    time.sleep(seconds_to_wait)
                start_time = datetime.now()
    except KeyboardInterrupt:
        print("Exiting...")