"""
app_dashboard.py
----------------
Máy chủ Web Dashboard Điều Khiển & Giám Sát Hệ Thống AI Agent MEXC Futures (XAU_USDT).
"""

import os
import sys
import json
import time
import asyncio
import logging
import requests
import webbrowser
import gzip
import websockets
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from quant_pricing_agent import QuantPricingAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WebDashboard")

app = FastAPI(title="MEXC AI Trading Terminal")

class TradingEngineState:
    def __init__(self):
        self.symbol = "XAU_USDT"
        self.mode = "DEMO (Paper Trading)"
        self.is_running = True
        self.initial_balance = 1000.0
        self.balance = 1000.0
        self.equity = 1000.0
        self.peak_equity = 1000.0
        self.max_drawdown = 0.0
        self.current_drawdown = 0.0
        self.is_alive = True
        self.death_reason = None
        
        self.leverage = 16.0
        self.margin_pct = 0.50
        self.fee_rate = 0.0005        # Phí Taker tiêu chuẩn BingX: 0.05%
        self.fee_rate_maker = 0.0002  # Phí Maker BingX (Chốt lời Limit TP1/TP2): 0.02%
        
        self.active_position = None
        self.trades: List[Dict[str, Any]] = []
        
        self.latest_price = 0.0
        self.price_change_24h = 0.0
        self.high_24h = 0.0
        self.low_24h = 0.0
        self.index_price = 0.0
        self.fair_price = 0.0
        self.funding_rate = 0.0050
        self.next_settle_time = 0
        self.volume_24h = 0.0
        self.amount_24h = 0.0
        self.last_trade_candle_time: int = 0
        self.candles: List[Dict[str, Any]] = []
        
        self.indicators = {
            "upper_bb": 0.0,
            "lower_bb": 0.0,
            "sma_bb": 0.0,
            "ema_1h": 0.0,
            "rsi": 50.0
        }
        
        self.quant_agent = QuantPricingAgent(
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_low=45.0,
            rsi_high=55.0,
            ema_1h_period=300,
            sl_pct=0.0025,
            tp1_pct=0.0055,
            tp2_pct=0.0110,
            lock_gain_ratio=0.25,
            max_daily_trades=4,
            max_daily_losses=2,
            filter_asia_morning=True
        )

state = TradingEngineState()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

def sync_market_and_step():
    try:
        url_ticker = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol=NCCOGOLD2USD-USDT"
        r_tick = requests.get(url_ticker, timeout=3)
        if r_tick.status_code == 200:
            d = r_tick.json().get("data", {})
            state.latest_price = float(d.get("lastPrice", state.latest_price or 4145.0))
            state.price_change_24h = float(d.get("priceChangePercent", 0.0))
            state.high_24h = float(d.get("highPrice", 0.0))
            state.low_24h = float(d.get("lowPrice", 0.0))
            state.index_price = state.latest_price
            state.fair_price = state.latest_price
            state.funding_rate = 0.0050
            state.next_settle_time = int(d.get("closeTime", 0))
            state.volume_24h = float(d.get("volume", 0.0))
            state.amount_24h = float(d.get("quoteVolume", 0.0))

        if state.active_position:
            side = state.active_position["side"]
            entry = state.active_position["entry_price"]
            size = state.active_position["size"]
            if side == "LONG":
                unrealized = (state.latest_price - entry) * size
            else:
                unrealized = (entry - state.latest_price) * size
            state.active_position["unrealized_pnl"] = unrealized
            state.active_position["pnl_pct"] = (unrealized / state.active_position["margin"]) * 100 if state.active_position["margin"] > 0 else 0
            state.equity = state.balance + unrealized
        else:
            state.equity = state.balance

        if state.equity > state.peak_equity:
            state.peak_equity = state.equity

        dd = (state.peak_equity - state.equity) / state.peak_equity if state.peak_equity > 0 else 0.0
        state.current_drawdown = dd * 100
        if dd * 100 > state.max_drawdown:
            state.max_drawdown = dd * 100

        if dd > 0.10 or state.equity <= 0.001:
            state.is_alive = False
            state.death_reason = "Drawdown vượt 10% (Permadeath)"

    except Exception as e:
        logger.error(f"[TICKER ERROR] {e}")

def load_initial_candles():
    try:
        url_kline = "https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol=NCCOGOLD2USD-USDT&interval=15m&limit=150"
        r = requests.get(url_kline, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            candles = []
            for item in data:
                candles.append({
                    "time": int(item["time"]) // 1000,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"])
                })
            
            for c in candles[-150:]:
                state.quant_agent.process_candle({
                    "symbol": "GOLD_USDT",
                    "timestamp": c["time"],
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"]
                })
                
            state.candles = candles[-80:]
            if candles:
                state.latest_price = candles[-1]["close"]
                prices = [c["close"] for c in candles]
                u, l, rsi, ema = state.quant_agent.calculate_indicators(prices)
                state.indicators = {
                    "upper_bb": round(u, 2),
                    "lower_bb": round(l, 2),
                    "sma_bb": round((u + l) / 2, 2),
                    "ema_1h": round(ema, 2),
                    "rsi": round(rsi, 1)
                }
            logger.info(f"[INIT READY] Đã nạp {len(state.candles)} nến 15m BingX.")
    except Exception as e:
        logger.error(f"[LOAD CANDLES ERROR] {e}")

def load_candles_sync():
    try:
        url_kline = "https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol=NCCOGOLD2USD-USDT&interval=15m&limit=100"
        r = requests.get(url_kline, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            candles = []
            for item in data:
                candles.append({
                    "time": int(item["time"]) // 1000,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"])
                })
            if candles:
                last_time = state.candles[-1]["time"] if state.candles else 0
                for c in candles:
                    if c["time"] > last_time:
                        state.quant_agent.process_candle({
                            "symbol": "GOLD_USDT",
                            "timestamp": c["time"],
                            "open": c["open"],
                            "high": c["high"],
                            "low": c["low"],
                            "close": c["close"],
                            "volume": c["volume"]
                        })
                state.candles = candles[-80:]
    except Exception as e:
        logger.error(f"[SYNC CANDLES ERROR] {e}")

def check_and_manage_live_position():
    if not state.active_position:
        return

    pos = state.active_position
    cur_p = state.latest_price
    side = pos["side"]
    entry = pos["entry_price"]
    size = pos["size"]
    tp1 = pos["tp1"]
    tp2 = pos["tp2"]
    sl = pos["sl"]
    tp1_hit = pos.get("tp1_hit", False)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if side == "LONG":
        # 1. Chốt lời toàn phần TP2 (+1.10%)
        if cur_p >= tp2:
            pnl = (cur_p - entry) * size
            fee = cur_p * size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP2 (+1.10%)",
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            state.active_position = None
            logger.info(f"[LIVE TP2 LONG] Hit @ {cur_p} | Net: +${net:.2f} (BingX Maker Fee)")

        # 2. Chốt 50% ở TP1 (+0.55%) và Dời SL vào vùng LÃI DƯƠNG (+0.14%)
        elif not tp1_hit and cur_p >= tp1:
            close_size = size * 0.50
            pnl = (cur_p - entry) * close_size
            fee = cur_p * close_size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            pos["size"] -= close_size
            pos["tp1_hit"] = True
            
            # Khóa lãi dương: entry + 25% khoảng cách TP1
            lock_dist = (tp1 - entry) * 0.25
            new_sl = round(entry + lock_dist, 2)
            pos["sl"] = new_sl
            pos["stop_loss"] = new_sl
            
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP1 (+0.55%)",
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            logger.info(f"[LIVE TP1 LONG] Hit @ {cur_p} | Dời SL vào LÃI DƯƠNG: {new_sl} (+0.14%)")

        # 3. Chạm Cắt lỗ / Khóa lãi dương
        elif cur_p <= sl:
            pnl = (cur_p - entry) * size
            fee = cur_p * size * state.fee_rate
            net = pnl - fee
            state.balance += net
            
            if tp1_hit:
                act_type = "PROFIT_LOCK"
                act_text = "KHÓA LÃI DƯƠNG (+0.14%)"
            else:
                act_type = "LOSS"
                act_text = "CẮT LỖ KỶ LUẬT (-0.25%)"
                state.quant_agent.daily_losses_count += 1
                if state.quant_agent.daily_losses_count >= state.quant_agent.max_daily_losses:
                    state.quant_agent.day_locked = True
                    logger.warning("[CIRCUIT BREAKER] Cầu dao tự ngắt: Dính 2 SL trong ngày!")

            state.trades.insert(0, {
                "time": now_str,
                "action": act_text,
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"{net:+.2f} USDT",
                "balance": f"${state.balance:.2f}",
                "type": act_type
            })
            state.active_position = None
            logger.info(f"[LIVE EXIT LONG] Hit SL @ {cur_p} ({act_text})")

    elif side == "SHORT":
        # 1. Chốt lời toàn phần TP2 (+1.10%)
        if cur_p <= tp2:
            pnl = (entry - cur_p) * size
            fee = cur_p * size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP2 (+1.10%)",
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            state.active_position = None
            logger.info(f"[LIVE TP2 SHORT] Hit @ {cur_p} | Net: +${net:.2f} (BingX Maker Fee)")

        # 2. Chốt 50% ở TP1 (+0.55%) và Dời SL vào vùng LÃI DƯƠNG (+0.14%)
        elif not tp1_hit and cur_p <= tp1:
            close_size = size * 0.50
            pnl = (entry - cur_p) * close_size
            fee = cur_p * close_size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            pos["size"] -= close_size
            pos["tp1_hit"] = True
            
            # Khóa lãi dương: entry - 25% khoảng cách TP1
            lock_dist = (entry - tp1) * 0.25
            new_sl = round(entry - lock_dist, 2)
            pos["sl"] = new_sl
            pos["stop_loss"] = new_sl
            
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP1 (+0.55%)",
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            logger.info(f"[LIVE TP1 SHORT] Hit @ {cur_p} | Dời SL vào LÃI DƯƠNG: {new_sl} (+0.14%)")

        # 3. Chạm Cắt lỗ / Khóa lãi dương
        elif cur_p >= sl:
            pnl = (entry - cur_p) * size
            fee = cur_p * size * state.fee_rate
            net = pnl - fee
            state.balance += net
            
            if tp1_hit:
                act_type = "PROFIT_LOCK"
                act_text = "KHÓA LÃI DƯƠNG (+0.14%)"
            else:
                act_type = "LOSS"
                act_text = "CẮT LỖ KỶ LUẬT (-0.25%)"
                state.quant_agent.daily_losses_count += 1
                if state.quant_agent.daily_losses_count >= state.quant_agent.max_daily_losses:
                    state.quant_agent.day_locked = True
                    logger.warning("[CIRCUIT BREAKER] Cầu dao tự ngắt: Dính 2 SL trong ngày!")

            state.trades.insert(0, {
                "time": now_str,
                "action": act_text,
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"{net:+.2f} USDT",
                "balance": f"${state.balance:.2f}",
                "type": act_type
            })
            state.active_position = None
            logger.info(f"[LIVE EXIT SHORT] Hit SL @ {cur_p} ({act_text})")

def check_live_entry_signal():
    if state.active_position is not None:
        return
    if state.quant_agent.day_locked or state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
        return
    if len(state.candles) < 30:
        return

    cur_candle = state.candles[-1]
    cur_time = cur_candle.get("time", 0)

    # KHÓA KỶ LUẬT: Tuyệt đối không mở quá 1 lệnh trên cùng 1 cây nến 15 phút!
    if state.last_trade_candle_time == cur_time:
        return

    # Lọc bẫy sáng phiên Á (06:00 - 08:59 VN)
    hour = datetime.now().hour
    if state.quant_agent.filter_asia_morning and (6 <= hour <= 8):
        return

    # Tính chỉ báo chuẩn xác từ các nến đóng + giá tick hiện tại
    prices = [c["close"] for c in state.candles[:-1]] + [state.latest_price]
    upper_bb, lower_bb, rsi, ema_1h = state.quant_agent.calculate_indicators(prices)
    state.indicators = {
        "upper_bb": round(upper_bb, 2),
        "lower_bb": round(lower_bb, 2),
        "sma_bb": round((upper_bb + lower_bb) / 2, 2),
        "ema_1h": round(ema_1h, 2),
        "rsi": round(rsi, 1)
    }

    close_p = state.latest_price
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ĐIỀU KIỆN LONG: Xu hướng Tăng (Close > EMA) + Kéo ngược chạm dải dưới + RSI hồi quy
    if close_p > ema_1h and close_p <= lower_bb and rsi <= state.quant_agent.rsi_low:
        margin = state.balance * state.margin_pct
        size = (margin * state.leverage) / close_p
        fee = close_p * size * state.fee_rate
        state.balance -= fee
        
        sl = round(close_p * (1.0 - state.quant_agent.sl_pct), 2)
        tp1 = round(close_p * (1.0 + state.quant_agent.tp1_pct), 2)
        tp2 = round(close_p * (1.0 + state.quant_agent.tp2_pct), 2)
        
        state.active_position = {
            "side": "LONG",
            "entry_price": close_p,
            "size": size,
            "margin": margin,
            "sl": sl,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "open_time": now_str,
            "unrealized_pnl": 0.0,
            "pnl_pct": 0.0
        }
        state.last_trade_candle_time = cur_time
        state.quant_agent.daily_trades_count += 1
        if state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
            state.quant_agent.day_locked = True
            
        state.trades.insert(0, {
            "time": now_str,
            "action": "VÀO LỆNH LONG",
            "price": close_p,
            "size": f"{size:.3f} oz",
            "pnl": f"-${fee:.2f} (Phí)",
            "balance": f"${state.balance:.2f}",
            "type": "OPEN"
        })
        state.trades = state.trades[:100]
        logger.info(f"[LIVE OPEN LONG] @ {close_p:.2f} | TP1: {tp1} | TP2: {tp2} | SL: {sl}")

    # ĐIỀU KIỆN SHORT: Xu hướng Giảm (Close < EMA) + Hồi phục chạm dải trên + RSI hồi quy
    elif close_p < ema_1h and close_p >= upper_bb and rsi >= state.quant_agent.rsi_high:
        margin = state.balance * state.margin_pct
        size = (margin * state.leverage) / close_p
        fee = close_p * size * state.fee_rate
        state.balance -= fee
        
        sl = round(close_p * (1.0 + state.quant_agent.sl_pct), 2)
        tp1 = round(close_p * (1.0 - state.quant_agent.tp1_pct), 2)
        tp2 = round(close_p * (1.0 - state.quant_agent.tp2_pct), 2)
        
        state.active_position = {
            "side": "SHORT",
            "entry_price": close_p,
            "size": size,
            "margin": margin,
            "sl": sl,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "open_time": now_str,
            "unrealized_pnl": 0.0,
            "pnl_pct": 0.0
        }
        state.last_trade_candle_time = cur_time
        state.quant_agent.daily_trades_count += 1
        if state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
            state.quant_agent.day_locked = True
            
        state.trades.insert(0, {
            "time": now_str,
            "action": "VÀO LỆNH SHORT",
            "price": close_p,
            "size": f"{size:.3f} oz",
            "pnl": f"-${fee:.2f} (Phí)",
            "balance": f"${state.balance:.2f}",
            "type": "OPEN"
        })
        logger.info(f"[LIVE OPEN SHORT] @ {close_p:.2f} | TP1: {tp1} | TP2: {tp2} | SL: {sl}")

async def bingx_realtime_ws_listener():
    url = "wss://open-api-swap.bingx.com/swap-market"
    sub_msg = {
        "id": "gold-ticker-stream",
        "reqType": "sub",
        "dataType": "NCCOGOLD2USD-USDT@ticker"
    }
    while True:
        try:
            logger.info("[BINGX WS] Đang kết nối trực tiếp luồng WebSocket BingX Realtime (Zero Latency)...")
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps(sub_msg))
                logger.info("[BINGX WS] KẾT NỐI THÀNH CÔNG! Đang stream giá Vàng BingX theo mili-giây.")
                while True:
                    raw = await ws.recv()
                    if isinstance(raw, bytes):
                        msg = gzip.decompress(raw).decode("utf-8")
                    else:
                        msg = raw
                    
                    if msg == "Ping":
                        await ws.send("Pong")
                        continue
                        
                    data = json.loads(msg)
                    if data.get("dataType") == "NCCOGOLD2USD-USDT@ticker" and data.get("data"):
                        d = data["data"]
                        new_price = float(d.get("c", state.latest_price or 4145.0))
                        state.latest_price = new_price
                        state.price_change_24h = float(d.get("P", 0.0))
                        state.high_24h = float(d.get("h", 0.0))
                        state.low_24h = float(d.get("l", 0.0))
                        state.volume_24h = float(d.get("v", 0.0))
                        state.amount_24h = float(d.get("q", 0.0))
                        state.index_price = new_price
                        state.fair_price = new_price
                        
                        # Cập nhật PnL và kiểm tra StopLoss/TakeProfit siêu tốc theo từng tick
                        if state.active_position:
                            side = state.active_position["side"]
                            entry = state.active_position["entry_price"]
                            size = state.active_position["size"]
                            if side == "LONG":
                                unrealized = (new_price - entry) * size
                            else:
                                unrealized = (entry - new_price) * size
                            state.active_position["unrealized_pnl"] = unrealized
                            state.active_position["pnl_pct"] = (unrealized / state.active_position["margin"]) * 100 if state.active_position["margin"] > 0 else 0
                            state.equity = state.balance + unrealized
                            
                            if state.equity > state.peak_equity:
                                state.peak_equity = state.equity
                            dd = (state.peak_equity - state.equity) / state.peak_equity if state.peak_equity > 0 else 0.0
                            state.current_drawdown = dd * 100
                            if dd * 100 > state.max_drawdown:
                                state.max_drawdown = dd * 100
                            if dd > 0.10 or state.equity <= 0.001:
                                state.is_alive = False
                                state.death_reason = "Drawdown vượt 10% (Permadeath)"
                                
                            check_and_manage_live_position()
                            
                        # Bắn ngay lập tức gói tin cập nhật tới tất cả trình duyệt qua WebSocket
                        await manager.broadcast(get_full_state_payload())
        except Exception as e:
            logger.warning(f"[BINGX WS WARNING] Mất kết nối luồng ({e}). Tự động kết nối lại sau 2 giây...")
            await asyncio.sleep(2)

async def background_trading_loop():
    last_candle_sync = 0
    while True:
        try:
            if state.is_running and state.is_alive:
                now_t = time.time()
                
                # 1. Định kỳ nạp nến mới từ BingX trong worker thread riêng (mỗi 10s)
                if now_t - last_candle_sync > 10:
                    await asyncio.to_thread(load_candles_sync)
                    last_candle_sync = now_t

                # 2. Quét tín hiệu mở lệnh mới khi đủ điều kiện
                check_live_entry_signal()

            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"[LOOP ERROR] {e}")
            await asyncio.sleep(1)

def get_full_state_payload() -> Dict[str, Any]:
    pnl_net = state.equity - state.initial_balance
    roi_pct = (pnl_net / state.initial_balance) * 100
    
    return {
        "symbol": state.symbol,
        "mode": state.mode,
        "is_running": state.is_running,
        "is_alive": state.is_alive,
        "death_reason": state.death_reason,
        "latest_price": state.latest_price,
        "price_change_24h": round(state.price_change_24h, 2),
        "high_24h": state.high_24h,
        "low_24h": state.low_24h,
        "index_price": round(state.index_price, 2),
        "fair_price": round(state.fair_price, 2),
        "funding_rate": round(state.funding_rate, 4),
        "volume_24h": state.volume_24h,
        "amount_24h": state.amount_24h,
        "balance": round(state.balance, 2),
        "equity": round(state.equity, 2),
        "net_pnl": round(pnl_net, 2),
        "roi_pct": round(roi_pct, 2),
        "current_drawdown": round(state.current_drawdown, 2),
        "max_drawdown": round(state.max_drawdown, 2),
        "leverage": state.leverage,
        "margin_pct": state.margin_pct * 100,
        "active_position": state.active_position,
        "indicators": state.indicators,
        "trades": state.trades[:15],
        "candles": state.candles[-80:],
        "radar": {
            "heartbeat": datetime.now().strftime("%H:%M:%S"),
            "cond_trend_long": state.latest_price > state.indicators.get("ema_1h", state.latest_price),
            "cond_bb_long": state.latest_price <= state.indicators.get("lower_bb", state.latest_price),
            "cond_rsi_long": state.indicators.get("rsi", 50.0) <= 45.0,
            "cond_trend_short": state.latest_price < state.indicators.get("ema_1h", state.latest_price),
            "cond_bb_short": state.latest_price >= state.indicators.get("upper_bb", state.latest_price),
            "cond_rsi_short": state.indicators.get("rsi", 50.0) >= 55.0,
            "daily_trades": state.quant_agent.daily_trades_count,
            "max_trades": state.quant_agent.max_daily_trades,
            "daily_losses": state.quant_agent.daily_losses_count,
            "max_losses": state.quant_agent.max_daily_losses,
            "day_locked": state.quant_agent.day_locked
        }
    }

@app.on_event("startup")
async def startup_event():
    load_initial_candles()
    sync_market_and_step()
    asyncio.create_task(bingx_realtime_ws_listener())
    asyncio.create_task(background_trading_loop())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await websocket.send_json(get_full_state_payload())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/state")
def api_get_state():
    return JSONResponse(get_full_state_payload())

class ControlRequest(BaseModel):
    action: str

@app.post("/api/control")
def api_control(req: ControlRequest):
    if req.action == "PAUSE":
        state.is_running = False
    elif req.action == "RESUME":
        state.is_running = True
    elif req.action == "RESET_DEMO":
        state.balance = 1000.0
        state.equity = 1000.0
        state.peak_equity = 1000.0
        state.max_drawdown = 0.0
        state.current_drawdown = 0.0
        state.active_position = None
        state.trades = []
        state.is_alive = True
        state.death_reason = None
    return {"status": "success", "action": req.action}

@app.post("/api/reset_account")
def api_reset_account():
    state.balance = 1000.0
    state.equity = 1000.0
    state.peak_equity = 1000.0
    state.max_drawdown = 0.0
    state.current_drawdown = 0.0
    state.is_alive = True
    state.death_reason = None
    state.active_position = None
    state.trades = []
    state.quant_agent.daily_trades_count = 0
    state.quant_agent.daily_losses_count = 0
    state.quant_agent.day_locked = False
    state.last_trade_candle_time = 0
    return {"status": "success", "message": "Đã đặt lại tài khoản Demo về $1,000"}

class ApiKeyRequest(BaseModel):
    api_key: str
    api_secret: str

@app.post("/api/save_keys")
def api_save_keys(req: ApiKeyRequest):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"BINGX_API_KEY={req.api_key.strip()}\n")
        f.write(f"BINGX_API_SECRET={req.api_secret.strip()}\n")
    state.mode = "LIVE TRADING"
    return {"status": "success", "message": "Đã lưu BingX API Keys an toàn vào .env cục bộ"}

INTERVAL_MAP = {
    "Min1": "1m", "Min5": "5m", "Min15": "15m", "Min30": "30m",
    "Min60": "1h", "Hour4": "4h", "Day1": "1d",
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"
}

@app.get("/api/kline")
def api_kline(interval: str = "Min15", limit: int = 120):
    try:
        bingx_int = INTERVAL_MAP.get(interval, "15m")
        url_kline = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol=NCCOGOLD2USD-USDT&interval={bingx_int}&limit={limit}"
        r = requests.get(url_kline, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            candles = []
            for item in data:
                candles.append({
                    "time": int(item["time"]) // 1000,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"])
                })
            return {"status": "success", "interval": interval, "candles": candles}
    except Exception as e:
        logger.error(f"[KLINE API ERROR] {e}")
    return {"status": "error", "interval": interval, "candles": []}

@app.post("/api/run_backtest")
def api_run_backtest():
    import backtest_engine
    configs = [
        {"name": "Gốc F7 (10x, 50% Tĩnh)", "lev": 10.0, "margin": 0.50, "dyn": False},
        {"name": "F8 ĐX 1 (12x, 45% - Siêu An Toàn)", "lev": 12.0, "margin": 0.45, "dyn": False},
        {"name": "F8 ĐX 2 (14x, 50% - Cân Bằng 1.5%/d)", "lev": 14.0, "margin": 0.50, "dyn": False},
        {"name": "Sniper Pro Max (16x, 50% - Đạt 1.85%/d)", "lev": 16.0, "margin": 0.50, "dyn": False},
    ]
    results = []
    for cfg in configs:
        engine = backtest_engine.BacktestEngineF8(
            initial_balance=1000.0,
            leverage=cfg["lev"],
            margin_pct=cfg["margin"],
            dynamic_compounding=cfg["dyn"],
            max_daily_trades=5,
            max_daily_losses=3,
            filter_asia_morning=True
        )
        res = engine.run_backtest(verbose=False)
        results.append({
            "name": cfg["name"],
            "lev": cfg["lev"],
            "margin": cfg["margin"] * 100,
            "roi_pct": round(res["roi_pct"], 2),
            "daily_roi": round(res["daily_roi"], 2),
            "max_drawdown": round(res["max_drawdown"], 2),
            "win_rate": round(res["win_rate"], 2),
            "is_alive": res["is_alive"]
        })
    return {"status": "success", "results": results}

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Không tìm thấy giao diện templates/index.html</h1>"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    if "RENDER" not in os.environ and port == 8000:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"http://127.0.0.1:{port}")
        import threading
        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("app_dashboard:app", host=host, port=port, reload=False)
