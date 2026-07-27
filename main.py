import requests
from datetime import datetime, timedelta, timezone
import time
import math
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import random
import http.client
import json
import os
import pytz
from dotenv import load_dotenv

load_dotenv()

# Timezone for news and trading
NY_TZ = pytz.timezone("America/New_York")

# --- Manual or Auto Fundamental Data (Forecast vs Previous) ---
# You can update these daily or automatically in future versions
currency_fundamentals = {
        # No data
}



# Add your news schedule with time (NY timezone)
currency_news_schedule = {
    "JPY": [
        datetime.now(NY_TZ).replace(hour=0, minute=30, second=0, microsecond=0),  # Tertiary Industry Activity
    ],
    "GBP": [
        datetime.now(NY_TZ).replace(hour=2, minute=0, second=0, microsecond=0),   # GDP, Construction, etc.
        datetime.now(NY_TZ).replace(hour=4, minute=30, second=0, microsecond=0),  # BOE Credit Conditions
        datetime.now(NY_TZ).replace(hour=9, minute=0, second=0, microsecond=0),   # MPC Mann Speaks
        datetime.now(NY_TZ).replace(hour=10, minute=45, second=0, microsecond=0), # MPC Mann again
        datetime.now(NY_TZ).replace(hour=14, minute=30, second=0, microsecond=0), # MPC Greene Speaks
    ],
    "CHF": [
        datetime.now(NY_TZ).replace(hour=3, minute=0, second=0, microsecond=0),   # SECO Forecasts
    ],
    "EUR": [
        datetime.now(NY_TZ).replace(hour=5, minute=0, second=0, microsecond=0),   # Trade Balance
        datetime.now(NY_TZ).replace(hour=12, minute=0, second=0, microsecond=0),  # ECB Lagarde Speaks
    ],
    "CAD": [
        datetime.now(NY_TZ).replace(hour=8, minute=15, second=0, microsecond=0),  # Housing Starts
        datetime.now(NY_TZ).replace(hour=13, minute=30, second=0, microsecond=0), # BOC Macklem Speaks
    ],
    "USD": [
        datetime.now(NY_TZ).replace(hour=8, minute=30, second=0, microsecond=0),  # Philly Fed Index
        datetime.now(NY_TZ).replace(hour=9, minute=0, second=0, microsecond=0),   # Multiple FOMC Speeches
        datetime.now(NY_TZ).replace(hour=10, minute=0, second=0, microsecond=0),  # Bowman Speaks
        datetime.now(NY_TZ).replace(hour=10, minute=30, second=0, microsecond=0), # NatGas Storage
        datetime.now(NY_TZ).replace(hour=12, minute=0, second=0, microsecond=0),  # Crude Oil Inventories
        datetime.now(NY_TZ).replace(hour=16, minute=15, second=0, microsecond=0), # Miran Speaks
        datetime.now(NY_TZ).replace(hour=18, minute=0, second=0, microsecond=0),  # Kashkari Speaks
    ],
    "ALL": [
        datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0),   # IMF Meetings
    ],
}



def get_fundamental_bias(currency):
    """Returns BUY, SELL, or NEUTRAL based on forecast vs previous and news time.
    Muted: the news schedule/forecast data below is stale and unmaintained, so this
    always returns NEUTRAL rather than blocking signals on outdated news windows."""
    return "NEUTRAL"

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

ROTATION_INTERVAL_SECONDS = 60  # 1 minute

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


# Generic fetch used by BOTH Twelve Data call sites (main fetch + higher-timeframe fetch) —
# parameterize the parts that differ (interval, outputsize, dp, etc) rather than duplicating
# the retry/rotation logic twice.
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
]

print("Tracking all available pairs automatically.")

price_history = {pair: [] for pair in pairs}

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
                    if raw is None:
                        continue
                    if "values" not in raw:
                        continue
                    df = pd.DataFrame(raw["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime")
                    df["close"] = df["close"].astype(float)
                    price = df["close"].iloc[-1]

                    # --- Calculate 15 technical indicators ---
                    # 1. RSI (14)
                    rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
                    rsi_values = rsi_indicator.rsi()
                    last_rsi = rsi_values.iloc[-1] if len(rsi_values) > 0 else None
                    # 2. EMA 20
                    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None
                    # 3. EMA 50
                    ema_50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(df) >= 50 else None
                    # 4. EMA 200
                    ema_200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-1] if len(df) >= 200 else None
                    # 5. MACD
                    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
                    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
                    macd = (ema_12 - ema_26).iloc[-1] if len(df) >= 26 else None
                    # 6. MACD Signal
                    macd_line = ema_12 - ema_26
                    macd_signal = macd_line.ewm(span=9, adjust=False).mean().iloc[-1] if len(df) >= 35 else None
                    # 7. Stochastic Oscillator %K
                    stoch = ta.momentum.StochasticOscillator(
                        high=df["close"], low=df["close"], close=df["close"], window=14, smooth_window=3
                    )
                    stoch_k = stoch.stoch().iloc[-1] if len(df) > 0 else None
                    # 8. Stochastic Oscillator %D
                    stoch_d = stoch.stoch_signal().iloc[-1] if len(df) > 0 else None
                    # 9. Bollinger Bands High
                    bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
                    bb_high = bb.bollinger_hband().iloc[-1] if len(df) > 0 else None
                    # 10. Bollinger Bands Low
                    bb_low = bb.bollinger_lband().iloc[-1] if len(df) > 0 else None
                    # 11. CCI (20)
                    cci = ta.trend.CCIIndicator(high=df["close"], low=df["close"], close=df["close"], window=20)
                    last_cci = cci.cci().iloc[-1] if len(df) > 0 else None
                    # 12. ADX (14)
                    adx = ta.trend.ADXIndicator(high=df["close"], low=df["close"], close=df["close"], window=14)
                    last_adx = adx.adx().iloc[-1] if len(df) > 0 else None
                    # 13. Williams %R
                    willr = ta.momentum.WilliamsRIndicator(high=df["close"], low=df["close"], close=df["close"], lbp=14)
                    last_willr = willr.williams_r().iloc[-1] if len(df) > 0 else None
                    # 14. ATR (14)
                    atr = ta.volatility.AverageTrueRange(high=df["close"], low=df["close"], close=df["close"], window=14)
                    last_atr = atr.average_true_range().iloc[-1] if len(df) > 0 else None
                    # 15. OBV
                    # For OBV, we need a 'volume' column; if not present, set to None
                    if "volume" in df.columns:
                        obv = ta.volume.OnBalanceVolumeIndicator(close=df["close"], volume=df["volume"])
                        last_obv = obv.on_balance_volume().iloc[-1] if len(df) > 0 else None
                    else:
                        last_obv = None

                    # Average True Range (ATR)
                    atr = ta.volatility.AverageTrueRange(high=df["close"], low=df["close"], close=df["close"], window=14)
                    last_atr = atr.average_true_range().iloc[-1] if len(df) > 0 else None

                    # Parabolic SAR
                    psar = ta.trend.PSARIndicator(high=df["close"], low=df["close"], close=df["close"], step=0.02, max_step=0.2)
                    psar_value = psar.psar().iloc[-1] if len(df) > 0 else None

                    if last_rsi is None or ema_20 is None or macd is None:
                        signal = "HOLD"
                    else:
                        # --- Loosened thresholds for 15 indicators to reduce HOLDs ---
                        rsi_status = "BUY" if last_rsi < 30 else "SELL" if last_rsi > 70 else "HOLD"
                        ema_status = "BUY" if price > ema_20 * 1.001 else "SELL" if price < ema_20 * 0.999 else "HOLD"
                        macd_status = "BUY" if macd > 0 else "SELL" if macd < 0 else "HOLD"
                        stoch_status = "BUY" if stoch_k is not None and stoch_k < 40 else "SELL" if stoch_k is not None and stoch_k > 50 else "HOLD"
                        bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.002 else "SELL" if bb_high is not None and price >= bb_high * 0.998 else "HOLD"
                        cci_status = "BUY" if last_cci is not None and last_cci < -70 else "SELL" if last_cci is not None and last_cci > 70 else "HOLD"
                        adx_status = "BUY" if last_adx is not None and last_adx > 12 and macd > 0 else "SELL" if last_adx is not None and last_adx > 12 and macd < 0 else "HOLD"

                        # ATR: compare to short-term average ATR to detect abnormally high volatility
                        atr_series = atr.average_true_range() if hasattr(atr, 'average_true_range') else None
                        avg_atr = None
                        if atr_series is not None and len(atr_series) >= 5:
                            avg_atr = atr_series.iloc[-5:].mean()
                        atr_status = "HOLD"
                        if last_atr is not None and avg_atr is not None:
                            # mark as BUY (volatility present) only if ATR noticeably above recent average
                            atr_status = "BUY" if last_atr > avg_atr * 1.02 else "HOLD"

                        # PSAR
                        psar_status = "BUY" if psar_value is not None and psar_value < price * 0.9995 else "SELL" if psar_value is not None and psar_value > price * 1.0005 else "HOLD"

                        # --- Multi-timeframe confirmation (4h) ---
                        def get_higher_timeframe_bias(symbol):
                            """Returns BUY/SELL/NEUTRAL bias for the 4h timeframe using 50 EMA trend."""
                            try:
                                raw_htf = fetch_twelve_data(symbol, interval="1min", outputsize=1000, dp=5)
                                if raw_htf is None or "values" not in raw_htf:
                                    return "NEUTRAL"
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

                        # --- 15-indicator scoring block ---
                        # 1. RSI (14)
                        # 2. EMA 20
                        # 3. EMA 50
                        # 4. EMA 200
                        # 5. MACD
                        # 6. MACD Signal
                        # 7. Stochastic %K
                        # 8. Stochastic %D
                        # 9. Bollinger Bands High
                        # 10. Bollinger Bands Low
                        # 11. CCI (20)
                        # 12. ADX (14)
                        # 13. Williams %R
                        # 14. ATR (14)
                        # 15. OBV

                        # --- Status determination for new indicators ---
                        # EMA50
                        ema50_status = "BUY" if ema_50 is not None and price > ema_50 * 1.001 else "SELL" if ema_50 is not None and price < ema_50 * 0.999 else "HOLD"
                        # EMA200
                        ema200_status = "BUY" if ema_200 is not None and price > ema_200 * 1.001 else "SELL" if ema_200 is not None and price < ema_200 * 0.999 else "HOLD"
                        # MACD Signal
                        macd_signal_status = "BUY" if macd_signal is not None and macd_signal > 0 else "SELL" if macd_signal is not None and macd_signal < 0 else "HOLD"
                        # Stochastic %D
                        stoch_d_status = "BUY" if stoch_d is not None and stoch_d < 40 else "SELL" if stoch_d is not None and stoch_d > 60 else "HOLD"
                        # Williams %R
                        willr_status = "BUY" if last_willr is not None and last_willr < -75 else "SELL" if last_willr is not None and last_willr > -25 else "HOLD"
                        # OBV (On-Balance Volume)
                        if last_obv is not None and len(df) >= 3:
                            obv_trend = last_obv - df["close"].iloc[-3]
                            obv_status = "BUY" if obv_trend > 0 else "SELL" if obv_trend < 0 else "HOLD"
                        else:
                            obv_status = "HOLD"

                        # Trend/momentum indicators carry more weight than the noisier
                        # oscillators, so the vote reflects trend strength, not just headcount.
                        indicator_weights = {
                            "rsi": 2,
                            "ema20": 3,
                            "ema50": 2,
                            "ema200": 2,
                            "macd": 2,
                            "macd_signal": 2,
                            "stoch_k": 1,
                            "stoch_d": 1,
                            "bb_high": 1,
                            "bb_low": 1,
                            "cci": 1,
                            "adx": 2,
                            "willr": 1,
                            "atr": 1,
                            "obv": 1,
                        }

                        indicator_statuses = {
                            "rsi": rsi_status,
                            "ema20": ema_status,
                            "ema50": ema50_status,
                            "ema200": ema200_status,
                            "macd": macd_status,
                            "macd_signal": macd_signal_status,
                            "stoch_k": stoch_status,
                            "stoch_d": stoch_d_status,
                            "bb_high": "SELL" if bb_high is not None and price >= bb_high * 0.9992 else "HOLD",
                            "bb_low": "BUY" if bb_low is not None and price <= bb_low * 1.0008 else "HOLD",
                            "cci": cci_status,
                            "adx": adx_status,
                            "willr": willr_status,
                            "atr": atr_status,
                            "obv": obv_status,
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

                        higher_tf_bias = get_higher_timeframe_bias(symbol)

                        # Voting: require a solid weighted majority and a margin over the
                        # other side, blocking only on a direct HTF conflict (not on NEUTRAL).
                        total_weight = sum(indicator_weights.values())
                        min_vote_share = 0.50
                        required_score = math.ceil(min_vote_share * total_weight)
                        min_margin = 3

                        if (
                            buy_score >= required_score
                            and (buy_score - sell_score) >= min_margin
                            and higher_tf_bias != "SELL"
                        ):
                            signal = f"BUY (score={buy_score}, HTF={higher_tf_bias})"
                        elif (
                            sell_score >= required_score
                            and (sell_score - buy_score) >= min_margin
                            and higher_tf_bias != "BUY"
                        ):
                            signal = f"SELL (score={sell_score}, HTF={higher_tf_bias})"
                        else:
                            signal = f"HOLD (score={hold_score}, HTF={higher_tf_bias})"

                    ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                    macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                    rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
                    stoch_str = f"{stoch_k:.2f}" if stoch_k is not None else "N/A"
                    bb_high_str = f"{bb_high:.5f}" if bb_high is not None else "N/A"
                    bb_low_str = f"{bb_low:.5f}" if bb_low is not None else "N/A"
                    cci_str = f"{last_cci:.2f}" if last_cci is not None else "N/A"
                    adx_str = f"{last_adx:.2f}" if last_adx is not None else "N/A"
                    atr_str = f"{last_atr:.5f}" if last_atr is not None else "N/A"
                    psar_str = f"{psar_value:.5f}" if psar_value is not None else "N/A"

                    print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | Stoch: {stoch_str} ({stoch_status}) | BB: Low {bb_low_str}, High {bb_high_str} ({bb_status}) | CCI: {cci_str} ({cci_status}) | ADX: {adx_str} ({adx_status}) | ATR: {atr_str} ({atr_status}) | PSAR: {psar_str} ({psar_status}) | Signal: {signal} | Weighted: BUY={buy_score}, SELL={sell_score}, HOLD={hold_score} | Fundamental Bias: {base_currency}={base_bias}, {quote_currency}={quote_bias}")

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
                            "time": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M:%S%z")
                        }

                        try:
                            log_path = "signals_log.csv"
                            record = {
                                "timestamp": datetime.now(ZoneInfo("America/New_York")).isoformat(),
                                "pair": symbol,
                                "action": signal.split()[0],
                                "full_signal": signal,
                                "price": price,
                                "rsi_status": rsi_status if 'rsi_status' in locals() else None,
                                "rsi_value": last_rsi,
                                "ema20_status": ema_status if 'ema_status' in locals() else None,
                                "ema20_value": ema_20,
                                "ema50_status": ema50_status if 'ema50_status' in locals() else None,
                                "ema50_value": ema_50,
                                "ema200_status": ema200_status if 'ema200_status' in locals() else None,
                                "ema200_value": ema_200,
                                "macd_status": macd_status if 'macd_status' in locals() else None,
                                "macd_value": macd,
                                "macd_signal_status": macd_signal_status if 'macd_signal_status' in locals() else None,
                                "macd_signal_value": macd_signal,
                                "stoch_k_status": stoch_status if 'stoch_status' in locals() else None,
                                "stoch_k_value": stoch_k,
                                "stoch_d_status": stoch_d_status if 'stoch_d_status' in locals() else None,
                                "stoch_d_value": stoch_d,
                                "bb_status": bb_status if 'bb_status' in locals() else None,
                                "bb_high_value": bb_high,
                                "bb_low_value": bb_low,
                                "cci_status": cci_status if 'cci_status' in locals() else None,
                                "cci_value": last_cci,
                                "adx_status": adx_status if 'adx_status' in locals() else None,
                                "adx_value": last_adx,
                                "willr_status": willr_status if 'willr_status' in locals() else None,
                                "willr_value": last_willr,
                                "atr_status": atr_status if 'atr_status' in locals() else None,
                                "atr_value": last_atr,
                                "obv_status": obv_status if 'obv_status' in locals() else None,
                                "obv_value": last_obv,
                                "buy_score": buy_score if 'buy_score' in locals() else None,
                                "sell_score": sell_score if 'sell_score' in locals() else None,
                                "hold_score": hold_score if 'hold_score' in locals() else None,
                                "htf_bias": higher_tf_bias if 'higher_tf_bias' in locals() else None,
                                "base_bias": base_bias,
                                "quote_bias": quote_bias,
                            }
                            df_log = pd.DataFrame([record])
                            write_header = not os.path.exists(log_path)
                            df_log.to_csv(log_path, mode="a", header=write_header, index=False)
                        except Exception as e:
                            print(f"⚠️ Error logging signal for {symbol}: {e}")

                        send_trade_signal(symbol, signal.split()[0], expiration_minutes)

                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cycle complete, sleeping 60 seconds.")
                time.sleep(60)
                start_time = datetime.now()  # Reset start time after each active cycle
            else:
                # After 20 minutes active, sleep until one hour from start_time
                next_cycle = start_time + pd.Timedelta(minutes=30)
                seconds_to_wait = (next_cycle - now).total_seconds()
                if seconds_to_wait > 0:
                    time.sleep(seconds_to_wait)
                start_time = datetime.now()
    except KeyboardInterrupt:
        print("Exiting...")