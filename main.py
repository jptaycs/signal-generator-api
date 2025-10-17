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

# Timezone for news and trading
NY_TZ = pytz.timezone("America/New_York")

# --- Manual or Auto Fundamental Data (Forecast vs Previous) ---
# You can update these daily or automatically in future versions
currency_fundamentals = {
    "JPY": {"forecast": -0.4, "previous": -0.2},  # Tertiary Industry Activity m/m
    "GBP": {"forecast": 0.1, "previous": 0.1},    # GDP m/m
    "EUR": {"forecast": 6.9, "previous": 5.3},    # Trade Balance
    "CHF": {"forecast": None, "previous": None},   # SECO Economic Forecasts
    "CAD": {"forecast": 258, "previous": 246},     # Housing Starts
    "USD": {"forecast": 8.6, "previous": 23.2},    # Philly Fed Manufacturing Index
    "AUD": {"forecast": None, "previous": None},   # No data today
    "NZD": {"forecast": None, "previous": None},   # No data today
    "CNY": {"forecast": None, "previous": None},   # No data today
}



# Add your news schedule with time (NY timezone)
currency_news_schedule = {
    "JPY": [
        datetime.now(NY_TZ).replace(hour=0, minute=30, second=0, microsecond=0),  # Tertiary Industry Activity
    ],
    "GBP": [
        datetime.now(NY_TZ).replace(hour=2, minute=0, second=0, microsecond=0),   # GDP & related data
        datetime.now(NY_TZ).replace(hour=9, minute=0, second=0, microsecond=0),   # MPC Member Mann Speaks
        datetime.now(NY_TZ).replace(hour=10, minute=45, second=0, microsecond=0), # Mann Speaks again
        datetime.now(NY_TZ).replace(hour=14, minute=30, second=0, microsecond=0), # MPC Member Greene Speaks
    ],
    "CHF": [
        datetime.now(NY_TZ).replace(hour=3, minute=0, second=0, microsecond=0),   # SECO Economic Forecasts
    ],
    "EUR": [
        datetime.now(NY_TZ).replace(hour=5, minute=0, second=0, microsecond=0),   # Trade Balance
        datetime.now(NY_TZ).replace(hour=12, minute=0, second=0, microsecond=0),  # ECB President Lagarde Speaks
    ],
    "CAD": [
        datetime.now(NY_TZ).replace(hour=8, minute=15, second=0, microsecond=0),  # Housing Starts
        datetime.now(NY_TZ).replace(hour=13, minute=30, second=0, microsecond=0), # BOC Gov Macklem Speaks
    ],
    "USD": [
        datetime.now(NY_TZ).replace(hour=8, minute=30, second=0, microsecond=0),  # Philly Fed Manufacturing Index
        datetime.now(NY_TZ).replace(hour=9, minute=0, second=0, microsecond=0),   # FOMC Speakers
        datetime.now(NY_TZ).replace(hour=10, minute=0, second=0, microsecond=0),  # NAHB Housing Index
        datetime.now(NY_TZ).replace(hour=10, minute=30, second=0, microsecond=0), # Natural Gas Storage
        datetime.now(NY_TZ).replace(hour=12, minute=0, second=0, microsecond=0),  # Crude Oil Inventories
        datetime.now(NY_TZ).replace(hour=16, minute=15, second=0, microsecond=0), # FOMC Member Miran Speaks
        datetime.now(NY_TZ).replace(hour=18, minute=0, second=0, microsecond=0),  # Kashkari Speaks
    ],
    "AUD": [
        datetime.now(NY_TZ).replace(hour=15, minute=45, second=0, microsecond=0), # RBA Gov Bullock Speaks
        datetime.now(NY_TZ).replace(hour=17, minute=50, second=0, microsecond=0), # RBA Assist Gov Kent Speaks
    ],
    "NZD": [
        datetime.now(NY_TZ).replace(hour=17, minute=45, second=0, microsecond=0), # FPI m/m
    ],
    "ALL": [
        datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0),   # IMF Meetings
    ],
    "CNY": [],
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


bot_token = "8119532010:AAHBTjlpUUgln260B1a2leDOu1oy6A2WnRo"
chat_id = "6460198665"


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





API_KEY = "e0c7cd3a05a448bda0c737c99cc4790f"
# Unli - e0c7cd3a05a448bda0c737c99cc4790f
# jptayco1109 - 652c4b836e0a44a8bb6c5b5004c7057c
# jptayco 2002 - 67a1d34cee5c4fe6a3bac7d5bc1bf864
# appnado - cf4fae9291334c638b4e71dc125a0863
# sweet - 62a2531773df4b6aa408b234041256d9
# sweetMain - 6debb834d8274930911045d03bf65673
# jp icloud - 2423e681b7314168a007bf8eb172f061
# cath - 41ab602809474f36985aadb6b849066e
# sweetgbox - 2988c838410642dbb950b62ad7505813

bot_token = "8119532010:AAHBTjlpUUgln260B1a2leDOu1oy6A2WnRo"
chat_id = "6460198665"  # Replace with your Telegram user ID or channel ID

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
                    base_bias = get_fundamental_bias(base_currency)
                    quote_bias = get_fundamental_bias(quote_currency)
                    url = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&interval=1min&outputsize=1000&dp=5&timezone=America/New_York&format=JSON"
                    import random

                    for attempt in range(5):  # up to 5 retries
                        try:
                            response = requests.get(url, timeout=10)
                            response.raise_for_status()
                            raw = response.json()
                            break
                        except requests.exceptions.RequestException as e:
                            print(f"⚠️ Connection issue for {symbol} (attempt {attempt + 1}/5): {e}")
                            time.sleep(2 ** attempt + random.random())  # exponential backoff
                    else:
                        print(f"❌ Failed to fetch data for {symbol} after 5 attempts.")
                        continue
                    if "values" not in raw:
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

                    # Average True Range (ATR)
                    atr = ta.volatility.AverageTrueRange(high=df["close"], low=df["close"], close=df["close"], window=14)
                    last_atr = atr.average_true_range().iloc[-1] if len(df) > 0 else None

                    # Parabolic SAR
                    psar = ta.trend.PSARIndicator(high=df["close"], low=df["close"], close=df["close"], step=0.02, max_step=0.2)
                    psar_value = psar.psar().iloc[-1] if len(df) > 0 else None

                    if last_rsi is None or ema_20 is None or macd is None:
                        signal = "HOLD"
                    else:
                        # --- Slightly looser indicator thresholds (reduce HOLDs) for 60-min expiration ---
                        # Loosened thresholds so more indicators produce BUY/SELL instead of HOLD
                        rsi_status = "BUY" if last_rsi < 23 else "SELL" if last_rsi > 82 else "HOLD"
                        ema_status = "BUY" if price > ema_20 * 1.0005 else "SELL" if price < ema_20 * 0.9995 else "HOLD"
                        macd_status = "BUY" if macd > 0.00015 else "SELL" if macd < -0.00015 else "HOLD"
                        stoch_status = "BUY" if stoch_k is not None and stoch_k < 40 else "SELL" if stoch_k is not None and stoch_k > 60 else "HOLD"
                        bb_status = "BUY" if bb_low is not None and price <= bb_low * 1.0008 else "SELL" if bb_high is not None and price >= bb_high * 0.9992 else "HOLD"
                        cci_status = "BUY" if last_cci is not None and last_cci < -80 else "SELL" if last_cci is not None and last_cci > 80 else "HOLD"
                        adx_status = "BUY" if last_adx is not None and last_adx > 18 and macd > 0 else "SELL" if last_adx is not None and last_adx > 18 and macd < 0 else "HOLD"

                        # ATR: compare to short-term average ATR to detect abnormally high volatility
                        atr_series = atr.average_true_range() if hasattr(atr, 'average_true_range') else None
                        avg_atr = None
                        if atr_series is not None and len(atr_series) >= 5:
                            avg_atr = atr_series.iloc[-5:].mean()
                        atr_status = "HOLD"
                        if last_atr is not None and avg_atr is not None:
                            # mark as BUY (volatility present) only if ATR noticeably above recent average
                            atr_status = "BUY" if last_atr > avg_atr * 1.05 else "HOLD"

                        # PSAR
                        psar_status = "BUY" if psar_value is not None and psar_value < price else "SELL" if psar_value is not None and psar_value > price else "HOLD"

                        # --- Multi-timeframe confirmation (4h) ---
                        def get_higher_timeframe_bias(symbol):
                            """Returns BUY/SELL/NEUTRAL bias for the 4h timeframe using 50 EMA trend."""
                            url_htf = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&interval=1min&outputsize=1000&dp=5&timezone=America/New_York&format=JSON"
                            try:
                                resp_htf = requests.get(url_htf, timeout=10)
                                resp_htf.raise_for_status()
                                raw_htf = resp_htf.json()
                                if "values" not in raw_htf:
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

                        # Indicator weights (unchanged)
                        indicator_weights = {
                            "rsi": 2,
                            "ema": 2,
                            "macd": 3,
                            "stoch": 2,
                            "bb": 2,
                            "cci": 2,
                            "adx": 2,
                            "atr": 2,
                            "psar": 2,
                        }

                        indicator_statuses = {
                            "rsi": rsi_status,
                            "ema": ema_status,
                            "macd": macd_status,
                            "stoch": stoch_status,
                            "bb": bb_status,
                            "cci": cci_status,
                            "adx": adx_status,
                            "atr": atr_status,
                            "psar": psar_status,
                        }

                        # Weighted scoring
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

                        # Final signal logic: reduced thresholds so not too strict
                        # Require reasonable weighted score (>=10) and avoid contradiction with HTF
                        if buy_score >= 14 or rsi_status == "BUY":
                            signal = f"BUY (score={buy_score}, HTF={higher_tf_bias})"
                        elif sell_score >= 14  or rsi_status == "SELL":
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