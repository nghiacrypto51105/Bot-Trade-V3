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
from typing import Dict, Any, List, Optional, Tuple
import hmac
import hashlib
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

app = FastAPI(title="BingX AI Trading Terminal")

class BingXAPIClient:
    """
    Client kết nối trực tiếp BingX Perpetual Swap API với xác thực HMAC-SHA256.
    Hỗ trợ: Kiểm tra số dư ví thực, cài đặt đòn bẩy 16x, đặt lệnh thị trường và đóng lệnh.
    """
    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = "https://open-api.bingx.com"

    def _sign(self, params: dict) -> str:
        params["timestamp"] = str(int(time.time() * 1000))
        params["recvWindow"] = "10000"
        sorted_items = sorted(params.items(), key=lambda d: d[0])
        query_str = "&".join([f"{k}={v}" for k, v in sorted_items])
        signature = hmac.new(self.api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query_str}&signature={signature}"

    def get_account_balance(self) -> Tuple[bool, float, float, str]:
        """
        Lấy số dư thực tế từ ví Perpetual Futures của BingX.
        Trả về: (thành_công, balance, equity, thông_điệp)
        """
        if not self.api_key or not self.api_secret:
            return False, 0.0, 0.0, "Chưa cung cấp API Key hoặc Secret Key"

        try:
            # Thử qua Swap v3 endpoint
            query = self._sign({})
            url = f"{self.base_url}/openApi/swap/v3/user/balance?{query}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                data = r.json()
                if data.get("code") == 0:
                    d = data.get("data", {})
                    b = d.get("balance", d)
                    if isinstance(b, dict):
                        bal = float(b.get("balance", b.get("availableMargin", 0.0)))
                        eq = float(b.get("equity", bal))
                        return True, bal, eq, "Thành công"
                    elif isinstance(b, list):
                        for item in b:
                            if item.get("asset") == "USDT":
                                bal = float(item.get("balance", item.get("availableMargin", 0.0)))
                                eq = float(item.get("equity", bal))
                                return True, bal, eq, "Thành công"
                        if b:
                            bal = float(b[0].get("balance", 0.0))
                            eq = float(b[0].get("equity", bal))
                            return True, bal, eq, "Thành công"
                else:
                    return False, 0.0, 0.0, data.get("msg", "Lỗi API BingX")

            # Fallback sang Swap v2 endpoint
            query_v2 = self._sign({})
            url_v2 = f"{self.base_url}/openApi/swap/v2/user/balance?{query_v2}"
            r2 = requests.get(url_v2, headers=headers, timeout=6)
            if r2.status_code == 200:
                data2 = r2.json()
                if data2.get("code") == 0:
                    d = data2.get("data", {})
                    b = d.get("balance", d)
                    if isinstance(b, dict):
                        bal = float(b.get("balance", b.get("availableMargin", 0.0)))
                        eq = float(b.get("equity", bal))
                        return True, bal, eq, "Thành công"
                return False, 0.0, 0.0, data2.get("msg", f"HTTP Error {r2.status_code}")

            return False, 0.0, 0.0, f"HTTP Error {r.status_code}"
        except Exception as e:
            return False, 0.0, 0.0, str(e)

    def set_leverage(self, symbol: str = "NCCOGOLD2USD-USDT", leverage: int = 16):
        if not self.api_key or not self.api_secret:
            return
        try:
            for side in ["LONG", "SHORT"]:
                q = self._sign({"symbol": symbol, "side": side, "leverage": str(leverage)})
                url = f"{self.base_url}/openApi/swap/v2/trade/leverage?{q}"
                headers = {"X-BX-APIKEY": self.api_key}
                requests.post(url, headers=headers, timeout=5)
            logger.info(f"[BINGX LEVERAGE] Đã cài đặt đòn bẩy {leverage}x cho {symbol}")
        except Exception as e:
            logger.warning(f"[BINGX LEVERAGE ERROR] {e}")

    def place_market_order(self, symbol: str, side: str, position_side: str, quantity: float) -> Tuple[bool, dict]:
        """
        Đặt lệnh thị trường trên sàn BingX.
        side: 'BUY' hoặc 'SELL'
        positionSide: 'LONG' hoặc 'SHORT'
        quantity: Khối lượng (tối thiểu 0.0005 oz)
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            qty_str = f"{max(0.0005, quantity):.4f}"
            params = {
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": qty_str
            }
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.post(url, headers=headers, timeout=6)
            res = r.json()
            if res.get("code") == 0:
                logger.info(f"[BINGX LIVE ORDER SUCCESS] {side} {position_side} {qty_str} | ID: {res.get('data', {}).get('orderId')}")
                return True, res.get("data", {})
            logger.error(f"[BINGX LIVE ORDER FAILED] {res}")
            return False, res
        except Exception as e:
            logger.error(f"[BINGX ORDER EXCEPTION] {e}")
            return False, {"error": str(e)}

def load_bingx_keys() -> Tuple[str, str]:
    api_key = os.getenv("BINGX_API_KEY", "")
    api_secret = os.getenv("BINGX_API_SECRET", "")
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BINGX_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                    elif line.startswith("BINGX_API_SECRET="):
                        api_secret = line.split("=", 1)[1].strip()
        except Exception:
            pass
    return api_key, api_secret

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

        self.bingx_client: Optional[BingXAPIClient] = None
        self.real_balance_synced: bool = False
        self.last_balance_sync: float = 0.0

state = TradingEngineState()

# Nạp sẵn BingX client nếu đã lưu keys trong .env
_init_k, _init_s = load_bingx_keys()
if _init_k and _init_s:
    state.bingx_client = BingXAPIClient(_init_k, _init_s)
    logger.info("[INIT] Đã tìm thấy BingX API Keys trong .env.")

def sync_live_balance() -> Tuple[bool, float, float, str]:
    """Đồng bộ số dư thực tế từ sàn BingX"""
    if state.bingx_client and state.bingx_client.api_key:
        ok, bal, eq, msg = state.bingx_client.get_account_balance()
        if ok:
            state.balance = bal
            state.equity = eq
            if not state.real_balance_synced:
                state.initial_balance = bal
                state.peak_equity = eq
                state.real_balance_synced = True
            return True, bal, eq, msg
        return False, state.balance, state.equity, msg
    return False, state.balance, state.equity, "Chưa cấu hình API Key"

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
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "SELL", "LONG", size)
                sync_live_balance()
            pnl = (cur_p - entry) * size
            fee = cur_p * size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP2 (+1.10%)" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
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
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "SELL", "LONG", close_size)
                sync_live_balance()
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
                "action": "CHỐT LỜI TP1 (+0.55%)" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            logger.info(f"[LIVE TP1 LONG] Hit @ {cur_p} | Dời SL vào LÃI DƯƠNG: {new_sl} (+0.14%)")

        # 3. Chạm Cắt lỗ / Khóa lãi dương
        elif cur_p <= sl:
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "SELL", "LONG", size)
                sync_live_balance()
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
                "action": act_text + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
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
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "BUY", "SHORT", size)
                sync_live_balance()
            pnl = (entry - cur_p) * size
            fee = cur_p * size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            state.trades.insert(0, {
                "time": now_str,
                "action": "CHỐT LỜI TP2 (+1.10%)" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
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
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "BUY", "SHORT", close_size)
                sync_live_balance()
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
                "action": "CHỐT LỜI TP1 (+0.55%)" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            logger.info(f"[LIVE TP1 SHORT] Hit @ {cur_p} | Dời SL vào LÃI DƯƠNG: {new_sl} (+0.14%)")

        # 3. Chạm Cắt lỗ / Khóa lãi dương
        elif cur_p >= sl:
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "BUY", "SHORT", size)
                sync_live_balance()
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
                "action": act_text + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"{net:+.2f} USDT",
                "balance": f"${state.balance:.2f}",
                "type": act_type
            })
            state.active_position = None
            logger.info(f"[LIVE EXIT SHORT] Hit SL @ {cur_p} ({act_text})")

def check_live_entry_signal():
    # 1. Luôn tính toán cập nhật các chỉ báo kỹ thuật theo giá tick mới nhất
    if len(state.candles) >= 30:
        prices = [c["close"] for c in state.candles[:-1]] + [state.latest_price]
        upper_bb, lower_bb, rsi, ema_1h = state.quant_agent.calculate_indicators(prices)
        state.indicators = {
            "upper_bb": round(upper_bb, 2),
            "lower_bb": round(lower_bb, 2),
            "sma_bb": round((upper_bb + lower_bb) / 2, 2),
            "ema_1h": round(ema_1h, 2),
            "rsi": round(rsi, 1)
        }

    # 2. Kiểm tra các bộ lọc sinh mệnh và kỷ luật trước khi vào lệnh
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

    close_p = state.latest_price
    ema_1h = state.indicators["ema_1h"]
    upper_bb = state.indicators["upper_bb"]
    lower_bb = state.indicators["lower_bb"]
    rsi = state.indicators["rsi"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ĐIỀU KIỆN LONG: Xu hướng Tăng (Close > EMA) + Kéo ngược chạm dải dưới + RSI hồi quy
    if close_p > ema_1h and close_p <= lower_bb and rsi <= state.quant_agent.rsi_low:
        margin = state.balance * state.margin_pct
        size = (margin * state.leverage) / close_p

        # Nếu đang ở chế độ LIVE TRADING: gửi lệnh thật lên sàn BingX
        if state.mode == "LIVE TRADING":
            if not state.bingx_client:
                logger.error("[LIVE ERROR] Chưa có BingX Client để mở lệnh Live!")
                return
            if state.balance < 2.0:
                logger.warning(f"[LIVE INSUFFICIENT BALANCE] Số dư ${state.balance:.2f} < 2 USDT tối thiểu!")
                return
            ok, res = state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "BUY", "LONG", size)
            if not ok:
                logger.error(f"[LIVE ORDER FAILED] Không thể khớp lệnh LONG BingX: {res}")
                return

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
            "action": "VÀO LỆNH LONG" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
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

        # Nếu đang ở chế độ LIVE TRADING: gửi lệnh thật lên sàn BingX
        if state.mode == "LIVE TRADING":
            if not state.bingx_client:
                logger.error("[LIVE ERROR] Chưa có BingX Client để mở lệnh Live!")
                return
            if state.balance < 2.0:
                logger.warning(f"[LIVE INSUFFICIENT BALANCE] Số dư ${state.balance:.2f} < 2 USDT tối thiểu!")
                return
            ok, res = state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "SELL", "SHORT", size)
            if not ok:
                logger.error(f"[LIVE ORDER FAILED] Không thể khớp lệnh SHORT BingX: {res}")
                return

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
            "action": "VÀO LỆNH SHORT" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
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

                # 2. Định kỳ đồng bộ số dư ví BingX thật mỗi 20 giây nếu đang ở chế độ LIVE
                if state.mode == "LIVE TRADING" and state.bingx_client and (now_t - state.last_balance_sync > 20):
                    if not state.active_position:
                        sync_live_balance()
                    state.last_balance_sync = now_t

                # 3. Quét tín hiệu mở lệnh mới khi đủ điều kiện
                check_live_entry_signal()

            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"[LOOP ERROR] {e}")
            await asyncio.sleep(1)

def compute_wait_reason() -> Dict[str, Any]:
    # 0. Kiểm tra số dư ví khi ở chế độ LIVE TRADING
    if state.mode == "LIVE TRADING" and state.balance < 2.0:
        return {
            "code": "LOW_BALANCE",
            "status_text": "SỐ DƯ BINGX QUÁ THẤP (< $2)",
            "badge_color": "#f6465d",
            "icon": "fa-wallet",
            "detail_text": f"Số dư tài khoản BingX hiện tại (${state.balance:.2f} USDT) chưa đủ mức ký quỹ tối thiểu ($2 USDT). Vui lòng nạp thêm USDT vào ví Hợp đồng BingX!"
        }

    # 1. Trạng thái dừng hệ thống
    if not state.is_running:
        return {
            "code": "PAUSED",
            "status_text": "TẠM DỪNG HOẠT ĐỘNG",
            "badge_color": "#8b949e",
            "icon": "fa-pause-circle",
            "detail_text": "Bot đang tạm dừng thủ công từ bảng điều khiển. Bấm 'TIẾP TỤC' để giao dịch."
        }

    # 2. Permadeath (cháy tài khoản hoặc sụt giảm >= 10%)
    if not state.is_alive:
        return {
            "code": "PERMADEATH",
            "status_text": "KÍCH HOẠT SINH TỬ (PERMADEATH)",
            "badge_color": "#f6465d",
            "icon": "fa-skull-crossbones",
            "detail_text": f"Dừng vĩnh viễn: {state.death_reason or 'Drawdown chạm 10%'}. Cần bấm Reset Demo để tái sinh tài khoản."
        }

    # 3. Đang có vị thế mở (Active Trade)
    if state.active_position is not None:
        pos = state.active_position
        side = pos.get("side", "N/A")
        pnl = pos.get("unrealized_pnl", 0.0)
        pnl_pct = pos.get("pnl_pct", 0.0)
        entry = pos.get("entry_price", 0.0)
        tp1 = pos.get("tp1", 0.0)
        sl = pos.get("sl", 0.0)
        pnl_sign = "+" if pnl >= 0 else ""
        return {
            "code": "IN_POSITION",
            "status_text": f"ĐANG GIỮ VỊ THẾ {side}",
            "badge_color": "#388bfd",
            "icon": "fa-crosshairs",
            "detail_text": f"Đang quản trị lệnh {side} tại ${entry:,.2f} | PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%) | TP1: ${tp1:,.2f} | SL: ${sl:,.2f}. Kỷ luật vàng: Tuyệt đối không nhồi lệnh!"
        }

    # 4. Cầu dao tự ngắt (Circuit Breaker - 2 SL liên tiếp / trong ngày)
    if state.quant_agent.daily_losses_count >= state.quant_agent.max_daily_losses or (state.quant_agent.day_locked and state.quant_agent.daily_losses_count > 0):
        return {
            "code": "CIRCUIT_BREAKER",
            "status_text": "CẦU DAO TỰ NGẮT (CIRCUIT BREAKER)",
            "badge_color": "#f6465d",
            "icon": "fa-ban",
            "detail_text": f"Đã chạm ngưỡng dừng lỗ tối đa hôm nay ({state.quant_agent.daily_losses_count}/{state.quant_agent.max_daily_losses} SL). Khóa lệnh bảo vệ vốn đến ngày mai!"
        }

    # 5. Hạn mức lệnh ngày (Daily trade limit - tối đa 4 lệnh)
    if state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
        return {
            "code": "DAILY_LIMIT_REACHED",
            "status_text": "ĐẠT HẠN MỨC NGÀY (4/4 LỆNH)",
            "badge_color": "#f0ad4e",
            "icon": "fa-shield-halved",
            "detail_text": f"Đã hoàn tất {state.quant_agent.daily_trades_count}/{state.quant_agent.max_daily_trades} lệnh theo chiến lược Sniper. Nghỉ ngơi bảo toàn trọn vẹn lợi nhuận!"
        }

    # 6. Dữ liệu nến chưa đủ
    if len(state.candles) < 30:
        return {
            "code": "INSUFFICIENT_DATA",
            "status_text": f"ĐANG NẠP DỮ LIỆU ({len(state.candles)}/30 NẾN)",
            "badge_color": "#8b949e",
            "icon": "fa-database",
            "detail_text": f"Đang đồng bộ nến 15m BingX ({len(state.candles)}/30). Cần tối thiểu 30 nến để tính EMA 300 và Bollinger Bands chuẩn xác."
        }

    # 7. Khóa cây nến hiện tại (Đã mở 1 lệnh trên cây nến 15m này)
    cur_candle = state.candles[-1] if state.candles else {}
    cur_time = cur_candle.get("time", 0)
    if state.last_trade_candle_time == cur_time and cur_time > 0:
        return {
            "code": "BAR_LOCKED",
            "status_text": "KHÓA NẾN HIỆN TẠI (1 LỆNH / 1 NẾN)",
            "badge_color": "#f0ad4e",
            "icon": "fa-lock",
            "detail_text": "Đã có 1 lệnh khớp trong cây nến 15m hiện tại. Đang đợi nến này đóng cửa để tránh bẫy whipsaw rủi ro."
        }

    # 8. Lọc bẫy sáng phiên Á (06:00 - 08:59 VN)
    hour = datetime.now().hour
    if state.quant_agent.filter_asia_morning and (6 <= hour <= 8):
        return {
            "code": "ASIA_MORNING_FILTER",
            "status_text": "LỌC BẪY PHIÊN Á (06:00 - 08:59)",
            "badge_color": "#a371f7",
            "icon": "fa-shield-alt",
            "detail_text": f"Hiện tại {hour:02d}:{datetime.now().minute:02d} VN (phiên Á thanh khoản mỏng, tỷ lệ bẫy Stop Hunt quét 2 đầu cao). Bot chủ động né tránh bẫy!"
        }

    # 9. Đang rình mồi thiết lập A+ (Stalking A+ Setup)
    p = state.latest_price
    ema = state.indicators.get("ema_1h", p)
    upper_bb = state.indicators.get("upper_bb", p)
    lower_bb = state.indicators.get("lower_bb", p)
    rsi = state.indicators.get("rsi", 50.0)

    is_long_trend = p > ema
    missing_conds = []

    if is_long_trend:
        trend_name = "LONG"
        if p > lower_bb:
            dist = p - lower_bb
            missing_conds.append(f"Chưa chạm BB Dưới (${lower_bb:,.1f}, còn cách ${dist:,.1f})")
        if rsi > state.quant_agent.rsi_low:
            missing_conds.append(f"RSI đang {rsi:.1f} (cần về <= {state.quant_agent.rsi_low})")
    else:
        trend_name = "SHORT"
        if p < upper_bb:
            dist = upper_bb - p
            missing_conds.append(f"Chưa chạm BB Trên (${upper_bb:,.1f}, còn cách ${dist:,.1f})")
        if rsi < state.quant_agent.rsi_high:
            missing_conds.append(f"RSI đang {rsi:.1f} (cần lên >= {state.quant_agent.rsi_high})")

    if missing_conds:
        reasons_str = " | ".join(missing_conds)
        return {
            "code": "WAITING_SETUP",
            "status_text": f"ĐANG RÌNH MỒI (ƯU TIÊN {trend_name})",
            "badge_color": "#f0ad4e",
            "icon": "fa-hourglass-half",
            "detail_text": f"Sóng lớn {trend_name} (Giá {'>' if is_long_trend else '<'} EMA 300). Đang đợi: {reasons_str}. AI kiên định chờ nến hoàn hảo!"
        }
    else:
        return {
            "code": "FIRING",
            "status_text": f"HỘI TỤ 3/3 {trend_name} - KHAI HỎA!",
            "badge_color": "#0ecb81",
            "icon": "fa-bolt",
            "detail_text": f"Tất cả điều kiện {trend_name} đã hội tụ đầy đủ. Hệ thống đang tiến hành mở lệnh!"
        }

def get_full_state_payload() -> Dict[str, Any]:
    pnl_net = state.equity - state.initial_balance
    roi_pct = (pnl_net / state.initial_balance) * 100
    wait_reason = compute_wait_reason()
    
    return {
        "symbol": state.symbol,
        "mode": state.mode,
        "is_live": state.mode == "LIVE TRADING",
        "has_keys": bool(state.bingx_client and state.bingx_client.api_key),
        "real_balance_synced": state.real_balance_synced,
        "is_running": state.is_running,
        "is_alive": state.is_alive,
        "death_reason": state.death_reason,
        "wait_reason": wait_reason,
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
            "wait_reason": wait_reason,
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

class ResetAccountRequest(BaseModel):
    balance: Optional[float] = 1000.0

@app.post("/api/reset_account")
def api_reset_account(req: Optional[ResetAccountRequest] = None):
    initial = 1000.0
    if req and req.balance and req.balance > 0:
        initial = float(req.balance)
    state.initial_balance = initial
    state.balance = initial
    state.equity = initial
    state.peak_equity = initial
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
    return {"status": "success", "message": f"Đã đặt lại tài khoản Demo về ${initial:,.2f}"}

class TestTradeRequest(BaseModel):
    side: str                          # "LONG" hoặc "SHORT"
    leverage: Optional[float] = 16.0   # Đòn bẩy tùy chọn (1x - 50x)
    margin_pct: Optional[float] = None # Ký quỹ % (0.05 - 1.0)
    margin_usdt: Optional[float] = None# Ký quỹ USDT cố định

@app.post("/api/test_trade")
def api_test_trade(req: TestTradeRequest):
    if state.mode == "LIVE TRADING":
        return JSONResponse({
            "status": "error",
            "message": "Tính năng mở lệnh thử nghiệm chỉ dùng trong chế độ DEMO để đảm bảo an toàn tuyệt đối cho tài khoản tiền thật!"
        })
    if state.active_position is not None:
        return JSONResponse({
            "status": "error",
            "message": "Đang có vị thế đang chạy! Vui lòng đóng lệnh hiện tại trước khi mở lệnh thử nghiệm mới."
        })
    
    close_p = state.latest_price
    if close_p <= 0 and state.candles:
        close_p = state.candles[-1]["close"]
    if close_p <= 0:
        close_p = 2650.0  # Fallback nếu chưa có giá từ sàn
        
    side = req.side.upper().strip()
    if side not in ["LONG", "SHORT"]:
        return JSONResponse({"status": "error", "message": "Hướng lệnh không hợp lệ (phải là LONG hoặc SHORT)"})
        
    lev = float(req.leverage) if (req.leverage and 1.0 <= req.leverage <= 50.0) else state.leverage
    if req.margin_usdt and 1.0 <= req.margin_usdt <= state.balance:
        margin = float(req.margin_usdt)
    elif req.margin_pct and 0.05 <= req.margin_pct <= 1.0:
        margin = state.balance * float(req.margin_pct)
    else:
        margin = state.balance * state.margin_pct
        
    size = (margin * lev) / close_p
    fee = close_p * size * state.fee_rate
    state.balance -= fee
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if side == "LONG":
        sl = round(close_p * (1.0 - state.quant_agent.sl_pct), 2)
        tp1 = round(close_p * (1.0 + state.quant_agent.tp1_pct), 2)
        tp2 = round(close_p * (1.0 + state.quant_agent.tp2_pct), 2)
    else:
        sl = round(close_p * (1.0 + state.quant_agent.sl_pct), 2)
        tp1 = round(close_p * (1.0 - state.quant_agent.tp1_pct), 2)
        tp2 = round(close_p * (1.0 - state.quant_agent.tp2_pct), 2)
        
    state.active_position = {
        "side": side,
        "entry_price": close_p,
        "size": size,
        "margin": margin,
        "leverage": lev,
        "sl": sl,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp1_hit": False,
        "open_time": now_str,
        "unrealized_pnl": 0.0,
        "pnl_pct": 0.0,
        "is_test": True
    }
    
    state.trades.insert(0, {
        "time": now_str,
        "action": f"VÀO LỆNH THỬ NGHIỆM ({side}) [DEMO]",
        "price": close_p,
        "size": f"{size:.3f} oz",
        "pnl": f"-${fee:.2f} (Phí)",
        "balance": f"${state.balance:.2f}",
        "type": "OPEN"
    })
    state.trades = state.trades[:100]
    logger.info(f"[TEST TRADE OPENED] {side} @ {close_p:.2f} | TP1: {tp1} | TP2: {tp2} | SL: {sl}")
    
    return JSONResponse({
        "status": "success",
        "message": f"Đã mở thành công lệnh THỬ NGHIỆM {side} tại giá ${close_p:,.2f}! Quan sát lệnh đang chạy ngay tại khung Vị Thế.",
        "position": state.active_position
    })

@app.post("/api/close_position")
def api_close_position():
    if not state.active_position:
        return JSONResponse({
            "status": "error",
            "message": "Hiện không có vị thế nào đang chạy để đóng."
        })
        
    pos = state.active_position
    cur_p = state.latest_price
    if cur_p <= 0 and state.candles:
        cur_p = state.candles[-1]["close"]
        
    side = pos["side"]
    entry = pos["entry_price"]
    size = pos["size"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if state.mode == "LIVE TRADING" and state.bingx_client:
        order_side = "SELL" if side == "LONG" else "BUY"
        state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", order_side, side, size)
        sync_live_balance()
        
    if side == "LONG":
        pnl = (cur_p - entry) * size
    else:
        pnl = (entry - cur_p) * size
        
    fee = cur_p * size * state.fee_rate
    net = pnl - fee
    state.balance += net
    
    trade_type = "WIN" if net >= 0 else "LOSS"
    act_name = "ĐÓNG LỆNH THỦ CÔNG" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else " [THỬ NGHIỆM]")
    
    state.trades.insert(0, {
        "time": now_str,
        "action": act_name,
        "price": cur_p,
        "size": f"{size:.3f} oz",
        "pnl": f"{net:+.2f} USDT",
        "balance": f"${state.balance:.2f}",
        "type": trade_type
    })
    state.trades = state.trades[:100]
    state.active_position = None
    logger.info(f"[MANUAL CLOSE] {side} closed @ {cur_p} | Net: {net:+.2f} USDT")
    
    return JSONResponse({
        "status": "success",
        "message": f"Đã đóng vị thế thị trường thành công tại giá ${cur_p:,.2f}! PnL ròng: {net:+.2f} USDT.",
        "balance": state.balance
    })

class SetModeRequest(BaseModel):
    mode: str                          # "LIVE" hoặc "DEMO"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

@app.post("/api/set_mode")
def api_set_mode(req: SetModeRequest):
    if req.mode == "LIVE":
        key = (req.api_key or "").strip()
        secret = (req.api_secret or "").strip()
        
        if not key or not secret:
            env_k, env_s = load_bingx_keys()
            if env_k and env_s:
                key, secret = env_k, env_s
            else:
                return JSONResponse({
                    "status": "need_keys",
                    "message": "Vui lòng nhập API Key & Secret Key để kết nối tài khoản BingX thực."
                })
        
        # Test kết nối & đồng bộ số dư thực tế
        client = BingXAPIClient(key, secret)
        ok, bal, eq, msg = client.get_account_balance()
        if not ok:
            return JSONResponse({
                "status": "error",
                "message": f"Kết nối BingX thất bại: {msg}. Vui lòng kiểm tra lại API Key, Secret và quyền Perpetual Futures!"
            })
            
        # Lưu vào .env để ghi nhớ cho các lần chạy sau
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"BINGX_API_KEY={key}\n")
                f.write(f"BINGX_API_SECRET={secret}\n")
        except Exception:
            pass
            
        client.set_leverage(symbol="NCCOGOLD2USD-USDT", leverage=16)
        
        state.bingx_client = client
        state.mode = "LIVE TRADING"
        state.initial_balance = bal
        state.balance = bal
        state.equity = eq
        state.peak_equity = eq
        state.max_drawdown = 0.0
        state.current_drawdown = 0.0
        state.active_position = None
        state.is_running = True
        state.is_alive = True
        state.death_reason = None
        state.real_balance_synced = True
        
        logger.info(f"[LIVE MODE ACTIVATED] Số dư đồng bộ từ BingX: ${bal:,.2f} USDT")
        return JSONResponse({
            "status": "success",
            "mode": "LIVE TRADING",
            "balance": bal,
            "equity": eq,
            "message": f"KẾT NỐI BINGX THÀNH CÔNG! Đã đồng bộ số dư thực: ${bal:,.2f} USDT. Bot bắt đầu tự động giao dịch LIVE!"
        })
        
    elif req.mode == "DEMO":
        state.mode = "DEMO (Paper Trading)"
        state.balance = 1000.0
        state.equity = 1000.0
        state.peak_equity = 1000.0
        state.max_drawdown = 0.0
        state.current_drawdown = 0.0
        state.active_position = None
        state.is_running = True
        state.is_alive = True
        state.death_reason = None
        state.real_balance_synced = False
        return JSONResponse({
            "status": "success",
            "mode": "DEMO (Paper Trading)",
            "balance": 1000.0,
            "message": "Đã chuyển về chế độ DEMO (Paper Trading) an toàn với số dư $1,000."
        })

@app.post("/api/sync_balance")
def api_sync_balance():
    if state.mode != "LIVE TRADING" or not state.bingx_client:
        return JSONResponse({
            "status": "not_live",
            "balance": state.balance,
            "equity": state.equity,
            "message": "Đang ở chế độ Demo, số dư giả lập tĩnh."
        })
    ok, bal, eq, msg = sync_live_balance()
    if ok:
        return JSONResponse({
            "status": "success",
            "balance": bal,
            "equity": eq,
            "message": f"Đã đồng bộ số dư mới nhất từ sàn BingX: ${bal:,.2f} USDT"
        })
    return JSONResponse({
        "status": "error",
        "balance": state.balance,
        "equity": state.equity,
        "message": f"Không thể đồng bộ số dư: {msg}"
    })

@app.get("/api/account_info")
def api_account_info():
    return JSONResponse({
        "mode": state.mode,
        "is_live": state.mode == "LIVE TRADING",
        "has_keys": bool(state.bingx_client and state.bingx_client.api_key),
        "balance": state.balance,
        "equity": state.equity
    })

class ApiKeyRequest(BaseModel):
    api_key: str
    api_secret: str

@app.post("/api/save_keys")
def api_save_keys(req: ApiKeyRequest):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"BINGX_API_KEY={req.api_key.strip()}\n")
        f.write(f"BINGX_API_SECRET={req.api_secret.strip()}\n")
    state.bingx_client = BingXAPIClient(req.api_key.strip(), req.api_secret.strip())
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

@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/ping", methods=["GET", "HEAD"])
def api_health():
    return JSONResponse({"status": "ok", "service": "bot-trade-v3", "alive": state.is_alive})

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
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
