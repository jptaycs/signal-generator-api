import requests
from datetime import datetime
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import threading
import websocket
import json

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
    "EUR/AUD", 
    "EUR/CAD",
    "EUR/CHF", 
    "EUR/GBP", 
    "GBP/AUD",
    "GBP/CAD",
    "GBP/CHF", 
    "GBP/USD", 
    "USD/CHF", 
    "USD/JPY", 
    "CHF/JPY", 
    "EUR/USD",
    "EUR/JPY",
    "GBP/JPY", 
    "USD/CAD",
    "BTC/USD",
    "ETH/USD",
    "LTC/USD",
    "BNB/USD",
    "SOL/USD",
    "DOGE/USD",
    "XRP/USD",
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

# Convert pairs to API symbol format (replace / with empty string)
api_symbols = [p.replace("/", "") for p in pairs]

# Data structures to hold historical data and latest prices
price_history = {pair: pd.DataFrame() for pair in pairs}
latest_prices = {pair: None for pair in pairs}

# Lock for thread-safe updates
data_lock = threading.Lock()

def fetch_historical_data():
    for symbol in pairs:
        url = f"https://api.twelvedata.com/time_series?apikey={API_KEY}&symbol={symbol}&interval=1min&outputsize=1000&dp=2&timezone=America/New_York&format=JSON"
        response = requests.get(url)
        raw = response.json()
        if "status" in raw and raw["status"] == "error":
            print(f"API error for {symbol}: {raw.get('message', 'Unknown error')}")
            continue
        if "values" not in raw:
            print(f"No historical data for {symbol}")
            continue
        df = pd.DataFrame(raw["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime")
        df["close"] = df["close"].astype(float)
        with data_lock:
            price_history[symbol] = df
            latest_prices[symbol] = df["close"].iloc[-1]

def calculate_indicators(df):
    if df.empty or len(df) < 26:
        return None, None, None
    
    rsi_indicator = ta.momentum.RSIIndicator(df["close"], window=14)
    rsi_values = rsi_indicator.rsi()
    last_rsi = rsi_values.iloc[-1] if len(rsi_values) > 0 else None

    ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1] if len(df) >= 20 else None

    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    macd = (ema_12 - ema_26).iloc[-1] if len(df) >= 26 else None

    return last_rsi, ema_20, macd

def generate_signal(price, last_rsi, ema_20, macd):
    if last_rsi is None or ema_20 is None or macd is None:
        return "HOLD"

    rsi_status = "BUY" if last_rsi < 48 else "SELL" if last_rsi > 52 else "HOLD"
    ema_status = "BUY" if price > ema_20 * 1.0001 else "SELL" if price < ema_20 * 0.9999 else "HOLD"
    macd_status = "BUY" if macd >= 0 else "SELL"

    statuses = [rsi_status, ema_status, macd_status]
    buy_count = statuses.count("BUY")
    sell_count = statuses.count("SELL")
    hold_count = statuses.count("HOLD")

    if buy_count >= 2:
        return f"BUY (score={buy_count})"
    elif sell_count >= 2:
        return f"SELL (score={sell_count})"
    else:
        return f"HOLD (score={hold_count})"

def print_signal(symbol, price, last_rsi, ema_20, macd, signal):
    rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
    ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
    macd_str = f"{macd:.5f}" if macd is not None else "N/A"

    rsi_status = "BUY" if last_rsi is not None and last_rsi < 48 else "SELL" if last_rsi is not None and last_rsi > 52 else "HOLD"
    ema_status = "BUY" if ema_20 is not None and price > ema_20 * 1.0001 else "SELL" if ema_20 is not None and price < ema_20 * 0.9999 else "HOLD"
    macd_status = "BUY" if macd is not None and macd >= 0 else "SELL" if macd is not None else "HOLD"

    print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} ({rsi_status}) | EMA20: {ema_str} ({ema_status}) | MACD: {macd_str} ({macd_status}) | Signal: {signal}")

def process_new_price(symbol, new_price):
    with data_lock:
        # Update price history by appending new price with current time
        df = price_history.get(symbol)
        now = pd.Timestamp.now(tz=ZoneInfo("America/New_York"))
        new_row = pd.DataFrame({"datetime": [now], "close": [new_price]})
        if df is not None and not df.empty:
            df = pd.concat([df, new_row], ignore_index=True)
            # Keep only last 1000 rows to limit size
            if len(df) > 1000:
                df = df.iloc[-1000:]
        else:
            df = new_row
        price_history[symbol] = df
        latest_prices[symbol] = new_price

        last_rsi, ema_20, macd = calculate_indicators(df)
        signal = generate_signal(new_price, last_rsi, ema_20, macd)
        print_signal(symbol, new_price, last_rsi, ema_20, macd, signal)

        if signal.startswith("BUY") or signal.startswith("SELL"):
            expiration_minutes = 5
            send_trade_signal(symbol, signal.split()[0], expiration_minutes)

def on_message(ws, message):
    data = json.loads(message)
    if "type" in data and data["type"] == "price":
        symbol = data.get("symbol")
        price = data.get("price")
        if symbol and price:
            # Convert symbol to pair format (e.g. AUDCAD -> AUD/CAD)
            pair = None
            for p in pairs:
                if p.replace("/", "") == symbol:
                    pair = p
                    break
            if pair:
                try:
                    price_float = float(price)
                    process_new_price(pair, price_float)
                except Exception as e:
                    print(f"Error processing price update for {pair}: {e}")

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed, attempting to reconnect in 5 seconds...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    print("WebSocket connection opened")

    # Subscribe to price updates for all symbols
    subscribe_payload = {
        "action": "subscribe",
        "params": {
            "symbols": ",".join(pairs),
            "fields": ["price"]
        }
    }
    ws.send(json.dumps(subscribe_payload))
    print("Subscribed to live price updates")

def run_websocket():
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(
        f"wss://ws.twelvedata.com/v1/quotes/price?apikey={API_KEY}",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws.run_forever()

def start_websocket():
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()

if __name__ == "__main__":
    try:
        fetch_historical_data()
        start_websocket()
        last_rest_fetch = time.time()
        while True:
            current_time = time.time()
            # Refresh historical data every 20 minutes
            if current_time - last_rest_fetch > 20 * 60:
                fetch_historical_data()
                last_rest_fetch = current_time

            # Sleep a bit to reduce CPU usage; real-time updates come from websocket
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")