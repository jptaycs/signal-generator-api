import requests
from datetime import datetime
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import random
import http.client
import json

from datetime import datetime, timedelta, timezone
import pytz

# Track event notifications and active blocks
notified_events = set()
blocked_currencies = {}  # e.g., { "USD": datetime_until_unblocked }

# Same timezone as your trading data
NY_TZ = pytz.timezone("America/New_York")

bot_token = "8119532010:AAHBTjlpUUgln260B1a2leDOu1oy6A2WnRo"
chat_id = "6460198665"


def send_telegram_message(text, markdown=True):
    """Helper to send Telegram alerts."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try:
        requests.post(url, data=payload, timeout=10)
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


def has_upcoming_news(currency, hours_ahead=2, block_hours_after=2):
    """
    Checks Forex Factory for any impact-level events in +/- 2 hours of current time.
    Blocks trading and notifies via Telegram.
    """

    now_ny = datetime.now(NY_TZ)

    # Check existing block
    if currency in blocked_currencies:
        if now_ny < blocked_currencies[currency]:
            print(f"⏸ {currency} still blocked until {blocked_currencies[currency].strftime('%H:%M')} NY.")
            return True
        else:
            print(f"✅ {currency} block expired — trading resumed.")
            del blocked_currencies[currency]

    # Prepare API request
    year, month, day = now_ny.year, now_ny.month, now_ny.day
    conn = http.client.HTTPSConnection("forex-factory-scraper1.p.rapidapi.com")
    headers = {
        'x-rapidapi-key': "82443e2586msh944f87d708095bfp14bdc9jsn68e056a59f6a",
        'x-rapidapi-host': "forex-factory-scraper1.p.rapidapi.com"
    }

    try:
        conn.request(
            "GET",
            f"/get_calendar_details?year={year}&month={month}&day={day}"
            f"&currency={currency}&event_name=ALL"
            f"&timezone=GMT-05:00%20America/New_York&time_format=24h",
            headers=headers
        )
        res = conn.getresponse()
        data = res.read()
        events = json.loads(data.decode("utf-8"))

        if not isinstance(events, dict) or "data" not in events:
            return False

        for ev in events["data"]:
            impact = ev.get("impact", "").title().strip()
            event_time = ev.get("time", "").strip()
            event_name = ev.get("event_name", "Unknown Event")

            if not event_time or "All Day" in event_time:
                continue

            try:
                event_hour, event_min = map(int, event_time.split(":"))
                event_dt = now_ny.replace(hour=event_hour, minute=event_min, second=0, microsecond=0)
                delta_hours = (event_dt - now_ny).total_seconds() / 3600

                # Ignore very old events
                if delta_hours < -block_hours_after:
                    continue

                # If within window before or after
                if -block_hours_after <= delta_hours <= hours_ahead:
                    event_key = f"{currency}_{event_name}_{event_time}"
                    if event_key not in notified_events:
                        notified_events.add(event_key)
                        unblock_time = event_dt + timedelta(hours=block_hours_after)
                        blocked_currencies[currency] = unblock_time

                        # Build notification
                        message = (
                            f"📰 *Forex News Alert*\n"
                            f"Currency: {currency}\n"
                            f"Event: {event_name}\n"
                            f"Impact: {impact}\n"
                            f"Time: {event_time} NY\n"
                            f"⏳ Trading paused until {unblock_time.strftime('%H:%M')} NY time."
                        )
                        print(message)
                        send_telegram_message(message)

                        # Also refresh the summary
                        show_news_status_summary()
                        return True

            except Exception:
                continue

        return False

    except Exception as e:
        print(f"⚠️ Failed to fetch news for {currency}: {e}")
        return False


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
            if elapsed < 60 * 60:
                for symbol in list(pairs):
                    base_currency = symbol.split("/")[0]
                    quote_currency = symbol.split("/")[1]
                    if has_upcoming_news(base_currency) or has_upcoming_news(quote_currency):
                        continue
                    url = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&interval=1h&outputsize=1000&dp=2&timezone=America/New_York&format=JSON"
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