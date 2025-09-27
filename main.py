from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import pandas as pd
import ta
from datetime import datetime, timedelta

chrome_options = Options()
## Uncomment the line below to run Chrome in headless mode.
# chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
driver = webdriver.Chrome(options=chrome_options)

pairs = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSDT"]
tabs = {}

driver.get("https://www.tradingview.com")
tabs["watchlist"] = driver.current_window_handle
time.sleep(2)

price_history = {pair: [] for pair in pairs}

def scrape_price(pair: str):
    driver.switch_to.window(tabs["watchlist"])
    normalized_pair = pair.upper().replace("/", "").replace(" ", "").replace("FX", "")
    try:
        rows = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.tv-data-table__row"))
        )
    except:
        return None
    for row in rows:
        try:
            spans = row.find_elements(By.TAG_NAME, "span")
            if not spans:
                continue
            span_texts = [span.text.strip() for span in spans]
            symbol_text = span_texts[0].upper().replace("/", "").replace(" ", "").replace("FX", "")
            if normalized_pair == symbol_text:
                try:
                    price_elems = row.find_elements(By.CSS_SELECTOR, "span.highlight-maJ2WnzA.highlight-BSF4XTsE.price-qWcO4bp9")
                    if not price_elems:
                        return None
                    price_text = "".join("".join(span.text for span in price_elem.find_elements(By.TAG_NAME, "span")) if price_elem.find_elements(By.TAG_NAME, "span") else price_elem.text for price_elem in price_elems).replace(",", "")
                    price = float(price_text)
                    return price
                except Exception as e:
                    return None
        except:
            continue
    return None

if __name__ == "__main__":
    try:
        while True:
            for symbol in pairs:
                price = scrape_price(symbol)
                if price is None:
                    print(f"{symbol}: Price not found")
                    continue
                history = price_history[symbol]
                history.append(price)
                if len(history) > 20:
                    history.pop(0)
                df = pd.DataFrame(history, columns=["close"])
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
                    if last_rsi < 30 and price > ema_20 and macd > 0:
                        signal = "BUY"
                    elif last_rsi > 70 and price < ema_20 and macd < 0:
                        signal = "SELL"
                    else:
                        signal = "HOLD"

                ema_str = f"{ema_20:.5f}" if ema_20 is not None else "N/A"
                macd_str = f"{macd:.5f}" if macd is not None else "N/A"
                rsi_str = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"

                print(f"{symbol} | Price: {price:.5f} | RSI: {rsi_str} | EMA20: {ema_str} | MACD: {macd_str} | Signal: {signal}")

                if signal in ("BUY", "SELL"):
                    expiration_time = (datetime.utcnow() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
                    trade_signal = {
                        "pair": symbol,
                        "action": signal,
                        "expiration": expiration_time
                    }
                    print(f"Trade Signal: {trade_signal}")

            time.sleep(60)
    except KeyboardInterrupt:
        print("Exiting...")
        driver.quit()