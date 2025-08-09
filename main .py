#!/usr/bin/env python3
"""
Precision Vix Bot (5m)
- Stochastic touch rule (no crossover)
- RSI confirmation (74/26)
- Bollinger Band confirmation (20,2)
- Sends Telegram alerts when a symbol changes signal
- Keep-alive Flask endpoint for Replit / UptimeRobot
"""

import time
import json
import logging
from datetime import datetime
from threading import Thread

import websocket
import pandas as pd
import numpy as np
import requests
from flask import Flask

# -----------------------
# Telegram (hardcoded as requested)
# -----------------------
BOT_TOKEN = "8015314103:AAHrRVSHzeK-f3M3qPAr-EKX9shQDqnQ0Gc"
CHAT_ID = "5567741626"

# -----------------------
# Deriv & Strategy config
# -----------------------
DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3"
DERIV_APP_ID = "1089"   # public app id — leave blank if you prefer
SYMBOLS = ["R_75", "R_75_1s", "R_100", "R_100_1s"]  # Four high-volatility picks
TIMEFRAME = 300  # 5 minutes
CANDLE_COUNT = 200

# Indicator params
RSI_PERIOD = 14
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
BOLL_PERIOD = 20
BOLL_STD = 2.0

RSI_OVERBOUGHT = 74.0
RSI_OVERSOLD = 26.0
STOCH_OVERBOUGHT = 92.5
STOCH_OVERSOLD = 7.5

CHECK_INTERVAL = TIMEFRAME  # run once per candle

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------
# Keep-alive (Flask) for Replit
# -----------------------
app = Flask('')

@app.route('/')
def home():
    return "✅ Precision Vix Bot is alive!"

def start_keep_alive():
    def run():
        app.run(host='0.0.0.0', port=8080)
    t = Thread(target=run, daemon=True)
    t.start()
    logging.info("Keep-alive server started")

# -----------------------
# Telegram helper
# -----------------------
def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logging.warning("Telegram send failed: %s", r.text)
    except Exception as e:
        logging.exception("Telegram exception: %s", e)

# -----------------------
# Fetch candles from Deriv (ephemeral ws)
# -----------------------
def fetch_candles(symbol: str, count: int = CANDLE_COUNT, granularity: int = TIMEFRAME):
    payload = {
        "ticks_history": symbol,
        "style": "candles",
        "granularity": granularity,
        "count": count,
        "end": "latest",
        "subscribe": 0
    }
    # build url with app id if present
    ws_url = DERIV_WS_URL
    if DERIV_APP_ID:
        if "?" in ws_url:
            ws_url = ws_url + "&app_id=" + DERIV_APP_ID
        else:
            ws_url = ws_url + "?app_id=" + DERIV_APP_ID

    try:
        ws = websocket.create_connection(ws_url, timeout=8)
        ws.send(json.dumps(payload))
        raw = ws.recv()
        ws.close()
        data = json.loads(raw)
    except Exception as e:
        logging.warning("Fetch candles error for %s: %s", symbol, e)
        return None

    if not data or 'history' not in data or 'candles' not in data['history']:
        logging.debug("No candles returned for %s: %s", symbol, data)
        return None

    candles = data['history']['candles']
    df = pd.DataFrame(candles)
    # ensure numeric types
    for c in ['open', 'high', 'low', 'close']:
        df[c] = df[c].astype(float)
    df['epoch'] = pd.to_datetime(df['epoch'], unit='s')
    df.set_index('epoch', inplace=True)
    return df

# -----------------------
# Indicators
# -----------------------
def compute_rsi(series: pd.Series, period: int = RSI_PERIOD):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-12)
    return 100 - (100 / (1 + rs))

def compute_stochastic(df: pd.DataFrame, k_period: int = STOCH_K_PERIOD, d_period: int = STOCH_D_PERIOD):
    low_min = df['low'].rolling(window=k_period, min_periods=1).min()
    high_max = df['high'].rolling(window=k_period, min_periods=1).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min + 1e-12)
    d = k.rolling(window=d_period, min_periods=1).mean()
    return k, d

def compute_bbands(series: pd.Series, period: int = BOLL_PERIOD, nstd: float = BOLL_STD):
    ma = series.rolling(window=period, min_periods=1).mean()
    std = series.rolling(window=period, min_periods=1).std().fillna(0)
    upper = ma + nstd * std
    lower = ma - nstd * std
    return upper, ma, lower

# -----------------------
# Strategy / Decision
# -----------------------
last_sent = {}  # track last signal per symbol to avoid duplicates

def analyze_symbol(symbol: str):
    df = fetch_candles(symbol)
    if df is None or len(df) < max(BOLL_PERIOD, RSI_PERIOD, STOCH_K_PERIOD) + 1:
        logging.info("[%s] not enough data", symbol)
        return

    df = df.copy()
    df['rsi'] = compute_rsi(df['close'], RSI_PERIOD)
    k, d = compute_stochastic(df, STOCH_K_PERIOD, STOCH_D_PERIOD)
    df['stoch_k'] = k
    df['stoch_d'] = d
    upper, mid, lower = compute_bbands(df['close'], BOLL_PERIOD, BOLL_STD)
    df['bb_upper'] = upper
    df['bb_mid'] = mid
    df['bb_lower'] = lower

    last = df.iloc[-1]

    # get values
    rsi_val = float(last['rsi'])
    stoch_k = float(last['stoch_k'])
    stoch_d = float(last['stoch_d'])
    close = float(last['close'])
    upper_bb = float(last['bb_upper'])
    lower_bb = float(last['bb_lower'])

    # stochastic touch condition (no crossover needed)
    touch_overb = (stoch_k >= STOCH_OVERBOUGHT) or (stoch_d >= STOCH_OVERBOUGHT)
    touch_overs = (stoch_k <= STOCH_OVERSOLD) or (stoch_d <= STOCH_OVERSOLD)

    signal = None
    reason = ""

    # SELL condition
    if touch_overb and (rsi_val >= RSI_OVERBOUGHT) and (close >= upper_bb):
        signal = "SELL"
        reason = f"Stoch_touch(OB) + RSI {rsi_val:.2f} >= {RSI_OVERBOUGHT} + close >= upper_BB"

    # BUY condition
    elif touch_overs and (rsi_val <= RSI_OVERSOLD) and (close <= lower_bb):
        signal = "BUY"
        reason = f"Stoch_touch(OS) + RSI {rsi_val:.2f} <= {RSI_OVERSOLD} + close <= lower_BB"

    # if signal detected and it's a new change, send
    if signal:
        prev = last_sent.get(symbol, {}).get("signal")
        if prev != signal:
            msg = build_message(symbol, signal, rsi_val, stoch_k, stoch_d, close, upper_bb, lower_bb, reason)
            send_telegram_message(msg)
            last_sent[symbol] = {"signal": signal, "ts": time.time()}
            logging.info("[%s] Sent %s | %s", symbol, signal, reason)
        else:
            logging.debug("[%s] Signal unchanged (%s) - skipping send", symbol, signal)
    else:
        logging.debug("[%s] No valid signal. RSI=%.2f StochK=%.2f StochD=%.2f close=%.5f", 
                      symbol, rsi_val, stoch_k, stoch_d, close)

def build_message(symbol, signal, rsi_val, stoch_k, stoch_d, close, upper_bb, lower_bb, reason):
    t = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"📊 *Precision Vix Bot*\n"
        f"*Symbol:* `{symbol}`\n"
        f"*Signal:* *{signal}*\n"
        f"*Time (UTC):* `{t}`\n"
        f"*Reason:* {reason}\n\n"
        f"*Indicators:*\n"
        f"• RSI: `{rsi_val:.2f}`\n"
        f"• Stoch K/D: `{stoch_k:.2f}` / `{stoch_d:.2f}`\n"
        f"• Close: `{close:.5f}`\n"
        f"• BB Upper: `{upper_bb:.5f}`  BB Lower: `{lower_bb:.5f}`\n\n"
        f"_Action:_ Enter at next 5m candle open. Suggested expiry: *10m*"
    )
    return msg

# -----------------------
# Main loop
# -----------------------
def run_loop():
    logging.info("Starting Precision Vix Bot for symbols: %s", SYMBOLS)
    # send startup test message
    send_telegram_message("🚀 Precision Vix Bot is now running (5m). Alerts will be sent here.")
    while True:
        try:
            for sym in SYMBOLS:
                try:
                    analyze_symbol(sym)
                except Exception as e:
                    logging.exception("Error analyzing %s: %s", sym, e)
            # sleep until next candle (we check every TIMEFRAME seconds)
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            logging.info("Stopped by user")
            break
        except Exception as e:
            logging.exception("Main loop error: %s", e)
            time.sleep(5)

if __name__ == "__main__":
    start_keep_alive()
    run_loop()
