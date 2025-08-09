import websocket
import json
import pandas as pd
import numpy as np
import requests
from flask import Flask
import threading
import time

# ======== TELEGRAM CONFIG ========
BOT_TOKEN = "8015314103:AAHrRVSHzeK-f3M3qPAr-EKX9shQDqnQ0Gc"
CHAT_ID = "5567741626"

# ======== DERIV CONFIG ========
SYMBOLS = [
    "R_75", "R_100", "R_50", "R_25", "R_10"
]  # You can adjust symbols if needed
TIMEFRAME = 300  # 5 minutes in seconds

# ======== STRATEGY SETTINGS ========
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
STOCH_OVERBOUGHT = 80
STOCH_OVERSOLD = 20

# ======== FLASK KEEP-ALIVE ========
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# ======== TELEGRAM ALERT ========
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("Telegram Error:", e)

# ======== RSI CALCULATION ========
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# ======== STOCHASTIC CALCULATION ========
def calculate_stochastic(high, low, close, k_period=14, d_period=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()

    stoch_k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    stoch_d = stoch_k.rolling(window=d_period).mean()
    return stoch_k, stoch_d

# ======== FETCH CANDLE DATA ========
def fetch_candles(symbol, count=100):
    ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089")
    request = {
        "ticks_history": symbol,
        "end": "latest",
        "count": count,
        "style": "candles",
        "granularity": TIMEFRAME
    }
    ws.send(json.dumps(request))
    data = json.loads(ws.recv())
    ws.close()
    candles = pd.DataFrame(data["candles"])
    candles["open"] = candles["open"].astype(float)
    candles["high"] = candles["high"].astype(float)
    candles["low"] = candles["low"].astype(float)
    candles["close"] = candles["close"].astype(float)
    return candles

# ======== STRATEGY CHECK ========
def check_market_signal():
    for symbol in SYMBOLS:
        try:
            candles = fetch_candles(symbol)
            close = candles["close"]
            high = candles["high"]
            low = candles["low"]

            rsi = calculate_rsi(close)
            stoch_k, stoch_d = calculate_stochastic(high, low, close)

            latest_rsi = rsi.iloc[-1]
            latest_k = stoch_k.iloc[-1]
            latest_d = stoch_d.iloc[-1]

            if latest_k < STOCH_OVERSOLD and latest_rsi < RSI_OVERSOLD:
                send_telegram_message(f"BUY Signal on {symbol}\nRSI: {latest_rsi:.2f} | Stoch K: {latest_k:.2f} | Stoch D: {latest_d:.2f}")
            elif latest_k > STOCH_OVERBOUGHT and latest_rsi > RSI_OVERBOUGHT:
                send_telegram_message(f"SELL Signal on {symbol}\nRSI: {latest_rsi:.2f} | Stoch K: {latest_k:.2f} | Stoch D: {latest_d:.2f}")

        except Exception as e:
            print(f"Error with {symbol}: {e}")

# ======== RUN BOT ========
def run_bot():
    while True:
        check_market_signal()
        time.sleep(300)  # Run every 5 minutes

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_bot()
