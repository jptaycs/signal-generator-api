import requests
from datetime import datetime
bot_token = "8119532010:AAHBTjlpUUgln260B1a2leDOu1oy6A2WnRo"
chat_id = "6460198665"  # Replace with your Telegram user ID or channel ID
def send_trade_signal(symbol, action, expiration_minutes):
    current_time = datetime.now().strftime("%H:%M")
    emoji = "🟩" if action.upper() == "BUY" else "🟥"
    message = (
        f"{symbol}\n"
        f"🕘 Expiration {expiration_minutes}M\n"
        f"⏺ Entry at {current_time}\n"
        f"{emoji} {action.upper()}"
    )
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        print("Telegram message sent successfully")
    else:
        print("Failed to send Telegram message:", response.text)
import time
import pandas as pd
import ta
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

API_KEY = "652c4b836e0a44a8bb6c5b5004c7057c"

pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP", "ETHUSD"]

print("Available pairs:")
for i, p in enumerate(pairs, start=1):
    print(f"{i}. {p}")
# Ask user which pair to track by index
choice = input(f"What pair do you want to track? (1-{len(pairs)}): ")
try:
    choice = int(choice)
except ValueError:
    print(f"Invalid input. Please enter an integer between 1 and {len(pairs)}.")
    exit(1)
if not (1 <= choice <= len(pairs)):
    print(f"Choice must be between 1 and {len(pairs)}.")
    exit(1)
pairs = [pairs[choice - 1]]

print(f"Tracking the following pairs: {pairs}")

price_history = {pair: [] for pair in pairs}

if __name__ == "__main__":
    try:
        while True:
            for symbol in pairs:
                if symbol == "ETHUSD":
                    formatted_symbol = "ETH/USD:Binance"
                else:
                    formatted_symbol = f"{symbol[:-3]}/{symbol[-3:]}"
                url = f"https://api.twelvedata.com/time_series?symbol={formatted_symbol}&interval=1min&apikey={API_KEY}&outputsize=30"
                response = requests.get(url)
                raw = response.json()
                if "values" not in raw:
                    print(f"{symbol}: No data returned")
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

                if last_rsi is None or ema_20 is None or macd is None:
                    signal = "HOLD"
                else:
                    # Determine status for each indicator
                    rsi_status = "BUY" if last_rsi < 40 else "SELL" if last_rsi > 60 else "HOLD"
                    ema_status = "BUY" if price > ema_20 * 1.0005 else "SELL" if price < ema_20 * 0.9995 else "HOLD"
                    macd_status = "BUY" if macd > 0.05 else "SELL" if macd < -0.05 else "HOLD"
                    statuses = [rsi_status, ema_status, macd_status]
                    if statuses.count("BUY") >= 2:
                        signal = "BUY"
                    elif statuses.count("SELL") >= 2:
                        signal = "SELL"
                    else:
                        signal = "HOLD"

                ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"

                print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | Signal: {signal}")

                if signal in ("BUY", "SELL"):
                    expiration_minutes = 1
                    expiration_time = f"{expiration_minutes} minutes"
                    trade_signal = {
                        "pair": formatted_symbol,
                        "action": signal,
                        "expiration": expiration_time,
                        "time": datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S %Z")
                    }
                    send_trade_signal(formatted_symbol, signal, expiration_minutes)

            # Wait until the start of the next minute
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            time.sleep(seconds_to_wait)
    except KeyboardInterrupt:
        print("Exiting...")