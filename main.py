import os
import requests
from datetime import datetime, timedelta, timezone, time as dt_time
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import random
import http.client
import json
import pytz
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

# --- Manual or Auto Fundamental Data (Forecast vs Previous) ---
# You can update these daily or automatically in future versions
currency_fundamentals = {
    # Latest news data; forecast/previous as per instructions.
    "USD": {"forecast": 54.1, "previous": 55.1},
    "EUR": {"forecast": -2.4, "previous": -0.3},
    "GBP": {"forecast": None, "previous": None},
    "JPY": {"forecast": None, "previous": None},
    "AUD": {"forecast": None, "previous": None},
    "CAD": {"forecast": 2.8, "previous": -65.5},
    "CHF": {"forecast": -37, "previous": -38},
    "NZD": {"forecast": None, "previous": None},
    "CNY": {"forecast": 8.5, "previous": 8.8},
}


# --- Forex news schedule ---
# All times are in NY timezone (America/New_York)
forex_news_schedule = [
    {"currency": "CHF", "event": "SECO Consumer Climate", "time": dt_time(3, 0)}, 
    {"currency": "EUR", "event": "Italian Industrial Production m/m", "time": dt_time(4, 0)}, 
    {"currency": "EUR", "event": "ECOFIN Meetings", "time": None},  # All day, block whole day
    {"currency": "CAD", "event": "Employment Change", "time": dt_time(8, 30)},
    {"currency": "CAD", "event": "Unemployment Rate", "time": dt_time(8, 30)},
    {"currency": "USD", "event": "FOMC Member Goolsbee Speaks", "time": dt_time(9, 45)},
    {"currency": "USD", "event": "Prelim UoM Consumer Sentiment", "time": dt_time(10, 0)},
    {"currency": "USD", "event": "Prelim UoM Inflation Expectations", "time": dt_time(10, 0)},
    {"currency": "USD", "event": "FOMC Member Musalem Speaks", "time": dt_time(13, 0)},
]

def is_currency_blocked(currency):
    """
    Returns True if current time is within ±1 hour of a scheduled news for the currency.
    Returns False otherwise.
    """
    now = datetime.now(NY_TZ)
    for news in forex_news_schedule:
        if news["currency"] != currency:
            continue
        if news["time"] is None:
            # All day event, block whole trading day
            return True, "ALL DAY"
        news_dt = now.replace(hour=news["time"].hour, minute=news["time"].minute, second=0, microsecond=0)
        delta_hours = (news_dt - now).total_seconds() / 3600
        if -1 <= delta_hours <= 1:
            return True, news["event"]
    return False, None


def get_fundamental_bias(currency):
    """
    Returns BUY, SELL, or NEUTRAL.
    - Within ±1 hour of scheduled news → follow forecast vs previous.
    - Outside ±1 hour → NEUTRAL.
    """
    now = datetime.now(NY_TZ)
    in_news_window = False

    for news in forex_news_schedule:
        if news["currency"] != currency:
            continue
        if news["time"] is None:
            # All-day event → always use forecast vs previous
            in_news_window = True
            break
        news_dt = now.replace(hour=news["time"].hour, minute=news["time"].minute, second=0, microsecond=0)
        delta_hours = (news_dt - now).total_seconds() / 3600
        if -1 <= delta_hours <= 1:
            in_news_window = True
            break

    if not in_news_window:
        return "NEUTRAL"

    # Inside ±1 hour of news → follow forecast vs previous
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


# Same timezone as your trading data
NY_TZ = pytz.timezone("America/New_York")

bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if not bot_token or not chat_id:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set in the environment. "
        "Copy .env.example to .env and fill in real values."
    )


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
            if elapsed < 60 * 60:

                for symbol in list(pairs):
                    base_currency, quote_currency = symbol.split("/")

                    # Always compute fundamental bias; use forecast vs previous inside ±1 hour, NEUTRAL otherwise
                    base_bias = get_fundamental_bias(base_currency)
                    quote_bias = get_fundamental_bias(quote_currency)
                    # Fetch with API key rotation and failover
                    raw = None
                    for _ in range(len(API_KEYS)):
                        key_info = get_active_key()
                        url = f"https://api.twelvedata.com/time_series?apikey={key_info['key']}&symbol={symbol}&interval=1h&outputsize=1000&dp=1&timezone=America/New_York&format=JSON"
                        try:
                            response = requests.get(url, timeout=10)
                        except requests.exceptions.RequestException as exc:
                            print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {exc}")
                            advance_key(reason=f"error on {symbol}")
                            continue

                        try:
                            raw = response.json()
                        except ValueError:
                            raw = {}

                        if response.status_code == 200 and raw.get("status") != "error" and "values" in raw:
                            break

                        error_msg = raw.get("message", response.text[:200])
                        print(f"[key-rotation] '{key_info['label']}' failed for {symbol}: {error_msg} (HTTP {response.status_code})")
                        advance_key(reason=f"error on {symbol}")

                    if raw is None or "values" not in raw:
                        print(f"[key-rotation] All API keys exhausted for {symbol}, skipping this cycle.")
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
                        rsi_status = "BUY" if last_rsi < 40 else "SELL" if last_rsi > 60 else "HOLD"
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

                        if buy_count >= 4 and sell_count <= 1 and (rsi_status == "BUY" or macd_status == "BUY"):
                            signal = f"BUY (score={buy_count})"
                        elif sell_count >= 4 and buy_count <= 1 and (rsi_status == "SELL" or macd_status == "SELL"):
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

                    print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | Stoch: {stoch_str} ({stoch_status}) | BB: Low {bb_low_str}, High {bb_high_str} ({bb_status}) | CCI: {cci_str} ({cci_status}) | ADX: {adx_str} ({adx_status}) | Signal: {signal} | Breakdown: BUY={buy_count}, SELL={sell_count}, HOLD={hold_count} | Fundamental Bias: {base_currency}={base_bias}, {quote_currency}={quote_bias}")

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