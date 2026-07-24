import os
import requests
from datetime import datetime
import time
import pandas as pd
import ta
from zoneinfo import ZoneInfo
import threading
import websocket
import json
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
    for _ in range(len(API_KEYS)):
        key_info = get_active_key()
        url = (
            f"https://api.twelvedata.com/time_series?apikey={key_info['key']}"
            f"&symbol={symbol}&interval=1min&outputsize=1000&dp=2"
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
        raw = fetch_time_series(symbol)
        if raw is None:
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
        f"wss://ws.twelvedata.com/v1/quotes/price?apikey={get_active_key()['key']}",
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