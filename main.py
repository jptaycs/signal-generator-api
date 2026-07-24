import os
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
from dotenv import load_dotenv

load_dotenv()

# Timezone for news and trading
NY_TZ = pytz.timezone("America/New_York")

# --- Manual or Auto Fundamental Data (Forecast vs Previous) ---
# You can update these daily or automatically in future versions
currency_fundamentals = {
    "EUR": {"previous": -0.5, "forecast": 0.1, "actual": -0.1},
    "CAD": {"previous": -0.6, "forecast": 0.5, "actual": 0.5},
    "USD": {"previous": -0.5, "forecast": 0.1, "actual": 0.1},
    "NZD": {"previous": None, "forecast": None, "actual": -1185},
    "AUD": {"previous": None, "forecast": None, "actual": None},
}



# Add your news schedule with time (NY timezone)
currency_news_schedule = {
    "EUR": [
        datetime.now(NY_TZ).replace(hour=2, minute=0, second=0, microsecond=0),   # German PPI m/m
        datetime.now(NY_TZ).replace(hour=4, minute=0, second=0, microsecond=0),   # Current Account
        datetime.now(NY_TZ).replace(hour=15, minute=0, second=0, microsecond=0),  # Buba President Nagel Speaks
    ],
    "CAD": [
        datetime.now(NY_TZ).replace(hour=8, minute=30, second=0, microsecond=0),  # IPPI, RMPI
        datetime.now(NY_TZ).replace(hour=10, minute=30, second=0, microsecond=0), # BOC Business Outlook Survey
    ],
    "USD": [
        datetime.now(NY_TZ).replace(hour=10, minute=0, second=0, microsecond=0),  # CB Leading Index m/m
    ],
    "NZD": [
        datetime.now(NY_TZ).replace(hour=17, minute=45, second=0, microsecond=0), # Trade Balance
        datetime.now(NY_TZ).replace(hour=22, minute=0, second=0, microsecond=0),  # Credit Card Spending y/y
    ],
    "AUD": [
        datetime.now(NY_TZ).replace(hour=19, minute=45, second=0, microsecond=0), # RBA Assist Gov Jones Speaks
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


# Generic fetch used by BOTH call sites (main 1-min fetch and the real 4h HTF fetch in
# get_higher_timeframe_bias) — parameterized by the parts that differ (interval,
# outputsize, dp) rather than duplicating the retry/rotation logic twice.
def fetch_twelve_data(symbol, interval, outputsize, dp, extra_params=""):
    for _ in range(len(API_KEYS)):
        key_info = get_active_key()
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval={interval}&outputsize={outputsize}&dp={dp}"
            f"&timezone=America/New_York&format=JSON{extra_params}"
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


# --- Higher timeframe bias function ---
def get_higher_timeframe_bias(symbol):
    """Return BUY, SELL, or NEUTRAL bias from the 4h timeframe using EMA50 trend."""
    raw_htf = fetch_twelve_data(symbol, interval="4h", outputsize=100, dp=5)
    if raw_htf is None or "values" not in raw_htf:
        return "NEUTRAL"
    try:
        df_htf = pd.DataFrame(raw_htf["values"])
        df_htf["datetime"] = pd.to_datetime(df_htf["datetime"])
        df_htf = df_htf.sort_values("datetime")
        df_htf["close"] = df_htf["close"].astype(float)
        if len(df_htf) < 50:
            return "NEUTRAL"
        ema_50 = df_htf["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        price_htf = df_htf["close"].iloc[-1]
        if price_htf > ema_50 * 1.0008:
            return "BUY"
        elif price_htf < ema_50 * 0.9992:
            return "SELL"
        else:
            return "NEUTRAL"
    except Exception as ex:
        print(f"⚠️ Higher timeframe fetch error for {symbol}: {ex}")
        return "NEUTRAL"

if __name__ == "__main__":
    try:
        start_time = datetime.now()
        while True:
            now = datetime.now()
            elapsed = (now - start_time).total_seconds()
            if elapsed < 15 * 60:
                for symbol in list(pairs):
                    base_currency, quote_currency = symbol.split("/")
                    base_bias = get_fundamental_bias(base_currency)
                    quote_bias = get_fundamental_bias(quote_currency)
                    raw = fetch_twelve_data(symbol, interval="1min", outputsize=1000, dp=5)
                    if raw is None or "values" not in raw:
                        continue
                    df = pd.DataFrame(raw["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime")
                    df["close"] = df["close"].astype(float)
                    price = df["close"].iloc[-1]

                    # --- Calculate 10 technical indicators (new, simplified & safer) ---
                    # We'll compute: RSI(14), MACD (+signal), EMA20 (kept for printing), EMA50, ADX(14),
                    # Bollinger Bands(20), ATR(14), OBV (if volume), MFI(14 if volume), PSAR, CCI(20)

                    # Keep computing EMA20 for backward compatibility with existing prints
                    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None

                    # RSI (14)
                    last_rsi = None
                    if len(df) >= 14:
                        rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
                        rsi_series = rsi_indicator.rsi()
                        last_rsi = float(rsi_series.iloc[-1])

                    # EMA 50 and EMA 200 (if available)
                    ema_50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(df) >= 50 else None
                    ema_200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-1] if len(df) >= 200 else None

                    # MACD and MACD signal (use EMAs)
                    macd = None
                    macd_signal = None
                    if len(df) >= 26:
                        ema_12 = df["close"].ewm(span=12, adjust=False).mean()
                        ema_26 = df["close"].ewm(span=26, adjust=False).mean()
                        macd_series = ema_12 - ema_26
                        macd = float(macd_series.iloc[-1])
                        if len(macd_series) >= 9:
                            macd_signal = float(macd_series.ewm(span=9, adjust=False).mean().iloc[-1])

                    # Bollinger Bands (20, 2)
                    bb_high = bb_low = None
                    if len(df) >= 20:
                        bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
                        bb_high = float(bb.bollinger_hband().iloc[-1])
                        bb_low = float(bb.bollinger_lband().iloc[-1])

                    # CCI (20)
                    last_cci = None
                    if len(df) >= 20:
                        cci = ta.trend.CCIIndicator(high=df["close"], low=df["close"], close=df["close"], window=20)
                        last_cci = float(cci.cci().iloc[-1])

                    # ADX (14)
                    last_adx = None
                    if len(df) >= 15:
                        try:
                            adx = ta.trend.ADXIndicator(high=df["close"], low=df["close"], close=df["close"], window=14)
                            last_adx = float(adx.adx().iloc[-1])
                        except Exception:
                            last_adx = None

                    # ATR (14)
                    last_atr = None
                    if len(df) >= 15:
                        atr_obj = ta.volatility.AverageTrueRange(high=df["close"], low=df["close"], close=df["close"], window=14)
                        last_atr = float(atr_obj.average_true_range().iloc[-1])

                    # PSAR (Parabolic SAR)
                    psar_value = None
                    if len(df) >= 2:
                        try:
                            psar = ta.trend.PSARIndicator(high=df["close"], low=df["close"], close=df["close"], step=0.02, max_step=0.2)
                            psar_value = float(psar.psar().iloc[-1])
                        except Exception:
                            psar_value = None

                    # Volume-based indicators (only if volume present)
                    last_obv = None
                    last_mfi = None
                    if "volume" in df.columns and df["volume"].notnull().any():
                        if len(df) >= 3:
                            try:
                                obv = ta.volume.OnBalanceVolumeIndicator(close=df["close"], volume=df["volume"]) 
                                last_obv = float(obv.on_balance_volume().iloc[-1])
                            except Exception:
                                last_obv = None
                        # MFI requires at least 14
                        if len(df) >= 14:
                            try:
                                mfi = ta.volume.MFIIndicator(high=df["close"], low=df["close"], close=df["close"], volume=df["volume"], window=14)
                                last_mfi = float(mfi.money_flow_index().iloc[-1])
                            except Exception:
                                last_mfi = None

                    # --- Build statuses with loosened thresholds to reduce HOLD outcomes ---
                    # Safety: ensure we have values before using them
                    rsi_status = "HOLD"
                    if last_rsi is not None:
                        rsi_status = "BUY" if last_rsi < 52 else ("SELL" if last_rsi > 48 else "HOLD")

                    ema20_status = "HOLD"
                    if ema_20 is not None:
                        ema20_status = "BUY" if price > ema_20 * 1.001 else ("SELL" if price < ema_20 * 0.999 else "HOLD")

                    ema50_status = "HOLD"
                    if ema_50 is not None:
                        ema50_status = "BUY" if price > ema_50 * 1.001 else ("SELL" if price < ema_50 * 0.999 else "HOLD")

                    macd_status = "HOLD"
                    if macd is not None:
                        macd_status = "BUY" if macd > 0 else ("SELL" if macd < 0 else "HOLD")

                    macd_signal_status = "HOLD"
                    if macd_signal is not None:
                        macd_signal_status = "BUY" if macd_signal > 0 else ("SELL" if macd_signal < 0 else "HOLD")

                    stoch_status = "HOLD"  # not computing Stoch now to keep this block compact

                    bb_status = "HOLD"
                    if bb_low is not None and bb_high is not None:
                        bb_status = "BUY" if price <= bb_low * 1.002 else ("SELL" if price >= bb_high * 0.998 else "HOLD")

                    cci_status = "HOLD"
                    if last_cci is not None:
                        cci_status = "BUY" if last_cci < -70 else ("SELL" if last_cci > 70 else "HOLD")

                    adx_status = "HOLD"
                    if last_adx is not None and macd is not None:
                        adx_status = "BUY" if last_adx > 12 and macd > 0 else ("SELL" if last_adx > 12 and macd < 0 else "HOLD")

                    atr_status = "HOLD"
                    if last_atr is not None:
                        # short-term compared to recent 5 values (if available)
                        try:
                            recent_atr = atr_obj.average_true_range() if 'atr_obj' in locals() else None
                            if recent_atr is not None and len(recent_atr) >= 5:
                                avg_atr = recent_atr.iloc[-5:].mean()
                                atr_status = "BUY" if last_atr > avg_atr * 1.02 else "HOLD"
                        except Exception:
                            atr_status = "HOLD"

                    psar_status = "HOLD"
                    if psar_value is not None:
                        psar_status = "BUY" if psar_value < price * 0.9995 else ("SELL" if psar_value > price * 1.0005 else "HOLD")

                    obv_status = "HOLD"
                    if last_obv is not None and len(df) >= 3:
                        # compare OBV to a value 3 bars ago (approx short-term trend)
                        try:
                            obv_trend = last_obv - float(ta.volume.OnBalanceVolumeIndicator(close=df["close"], volume=df["volume"]).on_balance_volume().iloc[-3])
                            obv_status = "BUY" if obv_trend > 0 else ("SELL" if obv_trend < 0 else "HOLD")
                        except Exception:
                            obv_status = "HOLD"

                    mfi_status = "HOLD"
                    if last_mfi is not None:
                        mfi_status = "BUY" if last_mfi < 40 else ("SELL" if last_mfi > 60 else "HOLD")

                    # --- Indicator weighting and scoring ---
                    indicator_weights = {
                        "rsi": 2,
                        "ema20": 1,
                        "ema50": 2,
                        "macd": 2,
                        "macd_signal": 1,
                        "bb": 1,
                        "cci": 1,
                        "adx": 2,
                        "atr": 1,
                        "psar": 1,
                        "obv": 1,
                        "mfi": 1,
                    }

                    indicator_statuses = {
                        "rsi": rsi_status,
                        "ema20": ema20_status,
                        "ema50": ema50_status,
                        "macd": macd_status,
                        "macd_signal": macd_signal_status,
                        "bb": bb_status,
                        "cci": cci_status,
                        "adx": adx_status,
                        "atr": atr_status,
                        "psar": psar_status,
                        "obv": obv_status,
                        "mfi": mfi_status,
                    }

                    buy_score = 0
                    sell_score = 0
                    hold_score = 0
                    for ind, status in indicator_statuses.items():
                        w = indicator_weights.get(ind, 1)
                        if status == "BUY":
                            buy_score += w
                        elif status == "SELL":
                            sell_score += w
                        else:
                            hold_score += w

                    # get higher timeframe bias (keeps the previous helper) and derive final signal
                    higher_tf_bias = get_higher_timeframe_bias(symbol)

                    # Final signal thresholds tuned for these 12 indicators
                    # require a reasonably strong buy score and minimal sell contradiction
                    if buy_score >= 8 and sell_score <= 3:
                        signal = f"BUY (score={buy_score}, HTF={higher_tf_bias})"
                    elif sell_score >= 8 and buy_score <= 3:
                        signal = f"SELL (score={sell_score}, HTF={higher_tf_bias})"
                    else:
                        signal = f"HOLD (score={hold_score}, HTF={higher_tf_bias})"

                    ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                    macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                    rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
                    bb_high_str = f"{bb_high:.5f}" if bb_high is not None else "N/A"
                    bb_low_str = f"{bb_low:.5f}" if bb_low is not None else "N/A"
                    cci_str = f"{last_cci:.2f}" if last_cci is not None else "N/A"
                    adx_str = f"{last_adx:.2f}" if last_adx is not None else "N/A"
                    atr_str = f"{last_atr:.5f}" if last_atr is not None else "N/A"
                    psar_str = f"{psar_value:.5f}" if psar_value is not None else "N/A"

                    # Ensure stoch_k is defined if referenced (not used here, but as per instructions)
                    stoch_k = None  # Define to avoid NameError if referenced elsewhere
                    print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema20_status}) | MACD: {macd_str} ({macd_status}) | BB: Low {bb_low_str}, High {bb_high_str} ({bb_status}) | CCI: {cci_str} ({cci_status}) | ADX: {adx_str} ({adx_status}) | ATR: {atr_str} ({atr_status}) | PSAR: {psar_str} ({psar_status}) | Signal: {signal} | Weighted: BUY={buy_score}, SELL={sell_score}, HOLD={hold_score} | Fundamental Bias: {base_currency}={base_bias}, {quote_currency}={quote_bias}")

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