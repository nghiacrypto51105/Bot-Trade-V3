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
from daily_pnl_tracker import tracker as pnl_tracker

def record_closed_trade_to_pnl(net: float, fee: float, trade_type: str, action: str, price: float, size_str: str, balance: float):
    try:
        pnl_tracker.record_trade(
            net_pnl=net,
            fee=fee,
            trade_type=trade_type,
            action=action,
            price=price,
            size_str=size_str,
            balance=balance
        )
    except Exception as e:
        logger.warning(f"[PNL RECORD ERROR] {e}")

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
        self.time_offset = 0
        self._sync_time()

    def _sync_time(self):
        try:
            r = requests.get(f"{self.base_url}/openApi/swap/v2/server/time", timeout=3)
            if r.status_code == 200:
                s_time = r.json().get("data", {}).get("serverTime")
                if s_time:
                    self.time_offset = int(s_time) - int(time.time() * 1000)
        except Exception:
            pass

    def _sign(self, params: dict) -> str:
        current_ts = int(time.time() * 1000) + self.time_offset
        params["timestamp"] = str(current_ts)
        params["recvWindow"] = "30000"
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
                    d = data.get("data", [])
                    if isinstance(d, list):
                        for item in d:
                            if isinstance(item, dict) and item.get("asset") == "USDT":
                                bal = float(item.get("balance", item.get("availableMargin", 0.0)))
                                eq = float(item.get("equity", bal))
                                return True, bal, eq, "Thành công"
                        if d and isinstance(d[0], dict):
                            bal = float(d[0].get("balance", 0.0))
                            eq = float(d[0].get("equity", bal))
                            return True, bal, eq, "Thành công"
                    elif isinstance(d, dict):
                        b = d.get("balance", d)
                        if isinstance(b, dict):
                            bal = float(b.get("balance", b.get("availableMargin", 0.0)))
                            eq = float(b.get("equity", bal))
                            return True, bal, eq, "Thành công"
                        elif isinstance(b, list):
                            for item in b:
                                if isinstance(item, dict) and item.get("asset") == "USDT":
                                    bal = float(item.get("balance", item.get("availableMargin", 0.0)))
                                    eq = float(item.get("equity", bal))
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
                    d2 = data2.get("data", {})
                    if isinstance(d2, dict):
                        b2 = d2.get("balance", d2)
                        if isinstance(b2, dict):
                            bal = float(b2.get("balance", b2.get("availableMargin", 0.0)))
                            eq = float(b2.get("equity", bal))
                            return True, bal, eq, "Thành công"
                    elif isinstance(d2, list):
                        for item in d2:
                            if isinstance(item, dict) and item.get("asset") == "USDT":
                                bal = float(item.get("balance", item.get("availableMargin", 0.0)))
                                eq = float(item.get("equity", bal))
                                return True, bal, eq, "Thành công"
                return False, 0.0, 0.0, data2.get("msg", f"HTTP Error {r2.status_code}")

            return False, 0.0, 0.0, f"HTTP Error {r.status_code}"
        except Exception as e:
            return False, 0.0, 0.0, str(e)

    def set_leverage(self, symbol: str = "NCCOGOLD2USD-USDT", leverage: int = 16) -> Tuple[bool, str]:
        if not self.api_key or not self.api_secret:
            return False, "Chưa cung cấp API Key BingX"
        self._sync_time()
        success = True
        msg = f"Đã cài đặt đòn bẩy {leverage}x thành công"
        for side in ["LONG", "SHORT"]:
            try:
                q = self._sign({"symbol": symbol, "side": side, "leverage": str(leverage)})
                url = f"{self.base_url}/openApi/swap/v2/trade/leverage?{q}"
                headers = {"X-BX-APIKEY": self.api_key}
                r = requests.post(url, headers=headers, timeout=5)
                res = r.json()
                if res.get("code") != 0:
                    success = False
                    msg = res.get("msg", f"Lỗi đặt đòn bẩy {side}")
                    logger.warning(f"[BINGX LEVERAGE ERROR] {side} {leverage}x: {msg}")
            except Exception as e:
                success = False
                msg = str(e)
                logger.warning(f"[BINGX LEVERAGE EXCEPTION] {e}")
        if success:
            logger.info(f"[BINGX LEVERAGE] Đã cài đặt đòn bẩy {leverage}x cho {symbol}")
        return success, msg

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
                data = res.get("data", {})
                order_info = data.get("order", data) if isinstance(data, dict) else {}
                order_id = order_info.get("orderId", order_info.get("orderID", data.get("orderId")))
                logger.info(f"[BINGX LIVE ORDER SUCCESS] {side} {position_side} {qty_str} | ID: {order_id}")
                return True, {"orderId": order_id, **order_info}
            logger.error(f"[BINGX LIVE ORDER FAILED] {res}")
            return False, res
        except Exception as e:
            logger.error(f"[BINGX ORDER EXCEPTION] {e}")
            return False, {"error": str(e)}

    def place_limit_order(self, symbol: str, side: str, position_side: str, quantity: float, price: float) -> Tuple[bool, dict]:
        """
        Đặt lệnh Limit Maker chờ khớp trên sàn BingX (Phí chỉ 0.02% - Tiết kiệm 60%).
        side: 'BUY' hoặc 'SELL'
        positionSide: 'LONG' hoặc 'SHORT'
        quantity: Khối lượng oz
        price: Mức giá Limit chờ khớp
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            qty_str = f"{max(0.0005, quantity):.4f}"
            price_str = f"{price:.2f}"
            params = {
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "LIMIT",
                "price": price_str,
                "quantity": qty_str,
                "timeInForce": "GTC"
            }
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.post(url, headers=headers, timeout=6)
            res = r.json()
            if res.get("code") == 0:
                data = res.get("data", {})
                order_info = data.get("order", data) if isinstance(data, dict) else {}
                order_id = order_info.get("orderId", order_info.get("orderID", data.get("orderId")))
                logger.info(f"[BINGX LIMIT ORDER PLACED] {side} {position_side} {qty_str} @ ${price_str} | ID: {order_id}")
                return True, {"orderId": order_id, **order_info}
            logger.error(f"[BINGX LIMIT ORDER FAILED] {res}")
            return False, res
        except Exception as e:
            logger.error(f"[BINGX LIMIT ORDER EXCEPTION] {e}")
            return False, {"error": str(e)}

    def cancel_order(self, symbol: str, order_id: str) -> Tuple[bool, dict]:
        """
        Hủy lệnh chờ Limit trên sàn BingX (Ví dụ khi hết hạn 15 phút hoặc gãy trend).
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            params = {
                "symbol": symbol,
                "orderId": str(order_id)
            }
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.delete(url, headers=headers, timeout=6)
            res = r.json()
            if res.get("code") == 0:
                logger.info(f"[BINGX CANCEL ORDER SUCCESS] Order ID: {order_id}")
                return True, res.get("data", {})
            logger.warning(f"[BINGX CANCEL ORDER FAILED] {res}")
            return False, res
        except Exception as e:
            logger.error(f"[BINGX CANCEL EXCEPTION] {e}")
            return False, {"error": str(e)}

    def get_order_status(self, symbol: str, order_id: str) -> Tuple[bool, dict]:
        """
        Lấy trạng thái thực tế của lệnh trên BingX (NEW, FILLED, CANCELED).
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            params = {
                "symbol": symbol,
                "orderId": str(order_id)
            }
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.get(url, headers=headers, timeout=6)
            res = r.json()
            if res.get("code") == 0:
                data = res.get("data", {})
                order_data = data.get("order", data)
                return True, order_data
            return False, res
        except Exception as e:
            return False, {"error": str(e)}

    def get_positions(self, symbol: str = "NCCOGOLD2USD-USDT") -> Tuple[bool, list, str]:
        """
        Lấy danh sách các vị thế đang mở thực tế trên sàn BingX.
        """
        if not self.api_key or not self.api_secret:
            return False, [], "Chưa có API Key"
        try:
            params = {"symbol": symbol}
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/user/positions?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                res = r.json()
                if res.get("code") == 0:
                    data = res.get("data", [])
                    open_pos = [p for p in data if float(p.get("positionAmt", 0)) != 0]
                    return True, open_pos, "Thành công"
                return False, [], res.get("msg", "Lỗi API BingX")
            return False, [], f"HTTP {r.status_code}"
        except Exception as e:
            return False, [], str(e)

    def close_all_positions(self, symbol: str = "NCCOGOLD2USD-USDT") -> Tuple[bool, dict]:
        """
        Đóng toàn bộ vị thế đang mở trên sàn BingX lập tức với lệnh thị trường (Market).
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            params = {"symbol": symbol}
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/closeAllPositions?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.post(url, headers=headers, timeout=6)
            res = r.json()
            if res.get("code") == 0:
                logger.info(f"[BINGX CLOSE ALL POSITIONS SUCCESS] Symbol: {symbol} | Res: {res.get('data')}")
                return True, res.get("data", {})
            logger.warning(f"[BINGX CLOSE ALL POSITIONS FAILED] {res}")
            return False, res
        except Exception as e:
            logger.error(f"[BINGX CLOSE ALL POSITIONS EXCEPTION] {e}")
            return False, {"error": str(e)}

    def get_open_orders(self, symbol: str = "NCCOGOLD2USD-USDT") -> Tuple[bool, list, str]:
        """
        Lấy danh sách các lệnh chờ / lệnh TP/SL đang mở trên sàn BingX.
        """
        if not self.api_key or not self.api_secret:
            return False, [], "Chưa có API Key"
        try:
            params = {"symbol": symbol}
            q = self._sign(params)
            url = f"{self.base_url}/openApi/swap/v2/trade/openOrders?{q}"
            headers = {"X-BX-APIKEY": self.api_key}
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                res = r.json()
                if res.get("code") == 0:
                    orders = res.get("data", {}).get("orders", [])
                    return True, orders, "Thành công"
                return False, [], res.get("msg", "Lỗi API")
            return False, [], f"HTTP {r.status_code}"
        except Exception as e:
            return False, [], str(e)

    def cancel_all_open_orders(self, symbol: str = "NCCOGOLD2USD-USDT") -> Tuple[bool, dict]:
        """
        Hủy tất cả các lệnh chờ / lệnh TP-SL cũ trên sàn BingX cho symbol (hủy từng ID triệt để).
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}
        try:
            ok, orders, _ = self.get_open_orders(symbol)
            if ok and orders:
                for o in orders:
                    oid = o.get("orderId")
                    if oid:
                        self.cancel_order(symbol, str(oid))
            return True, {"cancelled_count": len(orders) if ok else 0}
        except Exception as e:
            return False, {"error": str(e)}

    def set_position_tp_sl(self, symbol: str, position_side: str, quantity: float, sl_price: float = 0.0, tp_price: float = 0.0) -> Tuple[bool, dict]:
        """
        Cài đặt trực tiếp lệnh Cắt Lỗ (Stop Loss) và Chốt Lời (Take Profit) lên sàn BingX.
        Khóa chống spam lệnh: Tuyệt đối không đặt trùng nếu lệnh TP/SL đã tồn tại trên sàn.
        """
        if not self.api_key or not self.api_secret:
            return False, {"error": "Chưa có API Key"}

        # Kiểm tra các lệnh đang mở trên sàn để tránh spam/nhồi trùng lặp
        ok_orders, open_orders, _ = self.get_open_orders(symbol)
        has_sl = False
        has_tp = False
        if ok_orders and open_orders:
            for o in open_orders:
                if o.get("positionSide") == position_side:
                    if o.get("type") == "STOP_MARKET":
                        has_sl = True
                    elif o.get("type") == "TAKE_PROFIT_MARKET":
                        has_tp = True

        results = {}
        qty_str = f"{max(0.0005, quantity):.4f}"
        order_side = "SELL" if position_side == "LONG" else "BUY"

        # 1. Đặt lệnh Stop Loss lên sàn BingX (Chỉ đặt nếu chưa có)
        if sl_price > 0 and not has_sl:
            try:
                params_sl = {
                    "symbol": symbol,
                    "side": order_side,
                    "positionSide": position_side,
                    "type": "STOP_MARKET",
                    "stopPrice": f"{sl_price:.2f}",
                    "quantity": qty_str,
                    "workingType": "MARK_PRICE"
                }
                q = self._sign(params_sl)
                url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
                headers = {"X-BX-APIKEY": self.api_key}
                r = requests.post(url, headers=headers, timeout=6)
                res = r.json()
                results["sl"] = res
                if res.get("code") == 0:
                    logger.info(f"[BINGX SL ATTACHED] Đã gắn SL ${sl_price:.2f} trực tiếp lên sàn BingX! OrderID: {res.get('data', {}).get('order', {}).get('orderId')}")
                else:
                    logger.warning(f"[BINGX SL FAILED] {res}")
            except Exception as e:
                logger.error(f"[BINGX SL EXCEPTION] {e}")
                results["sl_error"] = str(e)

        # 2. Đặt lệnh Take Profit lên sàn BingX (Chỉ đặt nếu chưa có)
        if tp_price > 0 and not has_tp:
            try:
                params_tp = {
                    "symbol": symbol,
                    "side": order_side,
                    "positionSide": position_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": f"{tp_price:.2f}",
                    "quantity": qty_str,
                    "workingType": "MARK_PRICE"
                }
                q = self._sign(params_tp)
                url = f"{self.base_url}/openApi/swap/v2/trade/order?{q}"
                headers = {"X-BX-APIKEY": self.api_key}
                r = requests.post(url, headers=headers, timeout=6)
                res = r.json()
                results["tp"] = res
                if res.get("code") == 0:
                    logger.info(f"[BINGX TP ATTACHED] Đã gắn TP ${tp_price:.2f} trực tiếp lên sàn BingX! OrderID: {res.get('data', {}).get('order', {}).get('orderId')}")
                else:
                    logger.warning(f"[BINGX TP FAILED] {res}")
            except Exception as e:
                logger.error(f"[BINGX TP EXCEPTION] {e}")
                results["tp_error"] = str(e)

        return True, results

DEFAULT_BINGX_API_KEY = "npUSTPD0PKerLK8jZj3tFdSGxMozv6F8HqlEbuFrQDdWhYHsH84xZ5t6Isj4MLTi18jj3C2hvOX5fKqL4POEg"
DEFAULT_BINGX_SECRET_KEY = "RUPkdl0HBF4m6e7Thk6VyunJdfc4swyn8Glso7fMwDScSC6KfVJCT3MnBO520Hmevc6DWbWo6VrQ37a7QXw"

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
    if not api_key:
        api_key = DEFAULT_BINGX_API_KEY
    if not api_secret:
        api_secret = DEFAULT_BINGX_SECRET_KEY
    return api_key, api_secret

def save_bot_limits(trades: int = 0, losses: int = 0, locked: bool = False):
    """Lưu trạng thái giới hạn Sniper vào file JSON để không bị mất khi reload hoặc restart server"""
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "bot_limits.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "date": today_str,
                "daily_trades_count": trades,
                "daily_losses_count": losses,
                "day_locked": locked
            }, f, indent=2)
    except Exception as e:
        logger.error(f"[SAVE LIMITS ERROR] {e}")

def load_bot_limits() -> Tuple[int, int, bool]:
    """Tải trạng thái giới hạn Sniper đã lưu cho ngày hôm nay"""
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(os.path.dirname(__file__), "data", "bot_limits.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    return int(data.get("daily_trades_count", 0)), int(data.get("daily_losses_count", 0)), bool(data.get("day_locked", False))
    except Exception as e:
        logger.error(f"[LOAD LIMITS ERROR] {e}")
    return 0, 0, False

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
        self.risk_per_trade_pct = 0.025  # 2.5% max risk per trade (Optimal Pro Growth)
        self.max_margin_pct = 0.40       # 40% max margin cap
        self.margin_pct = 0.40
        self.fee_rate = 0.0005        # Phí Taker tiêu chuẩn BingX: 0.05%
        self.fee_rate_maker = 0.0002  # Phí Maker BingX (Chốt lời Limit TP1/TP2): 0.02%
        
        self.active_position = None
        self.pending_limit_order: Optional[Dict[str, Any]] = None
        self.order_ttl_seconds: int = 900  # 15 phút TTL tự động hủy lệnh lỗi thời
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
            tp1_pct=0.0048,
            tp2_pct=0.0110,
            be_trigger_pct=0.0038,
            lock_gain_ratio=0.25,
            max_daily_trades=8,
            max_daily_losses=2,
            filter_asia_morning=True
        )

        self.bingx_client: Optional[BingXAPIClient] = None
        self.real_balance_synced: bool = False
        self.last_balance_sync: float = 0.0

state = TradingEngineState()
_init_trades, _init_losses, _init_locked = load_bot_limits()
state.quant_agent.daily_trades_count = _init_trades
state.quant_agent.daily_losses_count = _init_losses
state.quant_agent.day_locked = _init_locked
logger.info(f"[INIT LIMITS] Giới hạn Sniper: {state.quant_agent.daily_trades_count}/{state.quant_agent.max_daily_trades} Lệnh | {state.quant_agent.daily_losses_count}/{state.quant_agent.max_daily_losses} SL")

# Nạp sẵn BingX client nếu đã lưu keys trong .env
_init_k, _init_s = load_bingx_keys()
if _init_k and _init_s:
    state.bingx_client = BingXAPIClient(_init_k, _init_s)
    logger.info("[INIT] Đã tìm thấy BingX API Keys trong .env.")
    try:
        ok, bal, eq, msg = state.bingx_client.get_account_balance()
        if ok:
            state.mode = "LIVE TRADING"
            state.initial_balance = bal
            state.balance = bal
            state.equity = eq
            state.peak_equity = eq
            state.real_balance_synced = True
            state.bingx_client.set_leverage(symbol="NCCOGOLD2USD-USDT", leverage=16)
            logger.info(f"[INIT LIVE] Đã kích hoạt LIVE TRADING! Số dư BingX: ${bal:,.2f} USDT | Đòn bẩy 16x")
    except Exception as e:
        logger.warning(f"[INIT LIVE ERROR] Không thể tự động đồng bộ Live: {e}")

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

def compute_smart_order_sizing(balance: float, close_p: float, leverage: float, sl_pct: float, risk_pct: float = 0.025, max_margin_pct: float = 0.40) -> Tuple[float, float, float]:
    """
    Quản trị vốn thông minh thích ứng với đòn bẩy cao (lên tới 500x).
    - Cố định rủi ro tối đa khi dính SL ở mức `risk_pct` (mặc định 2.5% tài khoản).
    - Phân bổ Ký quỹ linh hoạt tối đa 40% vốn, giữ >= 60% vốn tự do làm đệm chống cháy tuyệt đối.
    - Đảm bảo tuân thủ khối lượng tối thiểu BingX (0.0005 oz Vàng).
    """
    min_qty = 0.0005
    sl_distance = close_p * sl_pct
    max_risk_usd = balance * risk_pct
    ideal_size = max_risk_usd / sl_distance if sl_distance > 0 else min_qty
    size = max(min_qty, round(ideal_size, 4))
    
    pos_val = size * close_p
    required_margin = pos_val / max(1.0, leverage)
    
    cap_margin = balance * max_margin_pct
    if required_margin > cap_margin:
        required_margin = cap_margin
        size = max(min_qty, round((required_margin * leverage) / close_p, 4))
        
    actual_risk = size * sl_distance
    return size, required_margin, actual_risk

def compute_front_run_tp_sl(side: str, entry_p: float, sl_pct: float, tp1_pct: float, tp2_pct: float, upper_bb: float = 0.0, lower_bb: float = 0.0) -> Tuple[float, float, float]:
    """
    Tính toán SL, TP1, TP2 theo quy tắc Pro Trader:
    1. R:R luôn dưới 1:3 (TP1 = 0.48%, SL = 0.25%).
    2. Dynamic Front-Running S/R Buffer: Đặt TP1 trước vùng cản Kháng cự (Upper BB) / Hỗ trợ (Lower BB) ít nhất $2.0.
    """
    sl = round(entry_p * (1.0 - sl_pct) if side == "LONG" else entry_p * (1.0 + sl_pct), 2)
    ideal_tp1 = round(entry_p * (1.0 + tp1_pct) if side == "LONG" else entry_p * (1.0 - tp1_pct), 2)
    tp2 = round(entry_p * (1.0 + tp2_pct) if side == "LONG" else entry_p * (1.0 - tp2_pct), 2)

    if side == "LONG":
        if upper_bb > entry_p:
            res_buffer = round(upper_bb - 2.0, 2)
            if res_buffer > entry_p * 1.0020 and res_buffer < ideal_tp1:
                tp1 = res_buffer
            else:
                tp1 = ideal_tp1
        else:
            tp1 = ideal_tp1
    else:  # SHORT
        if lower_bb > 0 and lower_bb < entry_p:
            sup_buffer = round(lower_bb + 2.0, 2)
            if sup_buffer < entry_p * 0.9980 and sup_buffer > ideal_tp1:
                tp1 = sup_buffer
            else:
                tp1 = ideal_tp1
        else:
            tp1 = ideal_tp1

    return sl, tp1, tp2

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
            
            state.quant_agent.close_prices.clear()
            state.quant_agent.volume_history.clear()
            for c in candles[-150:]:
                state.quant_agent.close_prices.append(c["close"])
                state.quant_agent.volume_history.append(c["volume"])
                
            state.candles = candles[-80:]
            
            # Giữ nguyên giới hạn Sniper thực tế từ lưu trữ (không bị nến lịch sử làm sai lệch)
            saved_trades, saved_losses, saved_locked = load_bot_limits()
            state.quant_agent.daily_trades_count = saved_trades
            state.quant_agent.daily_losses_count = saved_losses
            state.quant_agent.day_locked = saved_locked
            state.quant_agent.active_position = None
            
            if candles:
                state.latest_price = candles[-1]["close"]
                prices = [c["close"] for c in candles]
                u, l, rsi, ema, rsi_fast = state.quant_agent.calculate_indicators(prices)
                state.indicators = {
                    "upper_bb": round(u, 2),
                    "lower_bb": round(l, 2),
                    "sma_bb": round((u + l) / 2, 2),
                    "ema_1h": round(ema, 2),
                    "rsi": round(rsi, 1),
                    "rsi_fast": round(rsi_fast, 1)
                }
            logger.info(f"[INIT READY] Đã nạp {len(state.candles)} nến 15m BingX. Giới hạn Sniper hiện tại: {state.quant_agent.daily_trades_count}/{state.quant_agent.max_daily_trades} Lệnh | {state.quant_agent.daily_losses_count}/{state.quant_agent.max_daily_losses} SL")
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
                        state.quant_agent.close_prices.append(c["close"])
                        state.quant_agent.volume_history.append(c["volume"])
                state.candles = candles[-80:]
    except Exception as e:
        logger.error(f"[SYNC CANDLES ERROR] {e}")

def sync_live_positions_with_bingx():
    """
    Đồng bộ hóa 2 chiều liên tục giữa Web và Sàn BingX:
    - Nếu sàn đã đóng vị thế (bằng TP/SL sàn hoặc đóng trên App điện thoại) -> Tự động xóa vị thế trên Web.
    - Nếu sàn có vị thế đang mở -> Đồng bộ chính xác khối lượng, giá vào và PnL thực tế.
    """
    if state.mode != "LIVE TRADING" or not state.bingx_client:
        return
    try:
        ok, open_pos, msg = state.bingx_client.get_positions("NCCOGOLD2USD-USDT")
        if not ok:
            return
        
        if not open_pos:
            if state.active_position is not None:
                logger.info("[LIVE SYNC] Vị thế trên sàn BingX đã được tất toán -> Đồng bộ xóa trên Web.")
                state.active_position = None
                sync_live_balance()
        else:
            p = open_pos[0]
            pos_amt = abs(float(p.get("positionAmt", 0)))
            pos_side = p.get("positionSide", "LONG")
            avg_price = float(p.get("avgPrice", state.latest_price or 4150.0))
            unrealized = float(p.get("unrealizedProfit", 0))
            margin = float(p.get("initialMargin", p.get("margin", 5.0)))
            leverage = int(p.get("leverage", state.leverage))
            
            if state.active_position is None:
                upper_bb = state.indicators.get("upper_bb", 0.0)
                lower_bb = state.indicators.get("lower_bb", 0.0)
                sl_calc, tp1_calc, tp2_calc = compute_front_run_tp_sl(pos_side, avg_price, state.quant_agent.sl_pct, state.quant_agent.tp1_pct, state.quant_agent.tp2_pct, upper_bb, lower_bb)
                state.active_position = {
                    "side": pos_side,
                    "entry_price": avg_price,
                    "size": pos_amt,
                    "margin": margin,
                    "leverage": leverage,
                    "sl": sl_calc,
                    "stop_loss": sl_calc,
                    "tp1": tp1_calc,
                    "tp2": tp2_calc,
                    "tp1_hit": False,
                    "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "strategy": "BINGX SYNCED",
                    "order_type": "LIVE_SYNC",
                    "unrealized_pnl": unrealized,
                    "pnl_pct": (unrealized / margin) * 100 if margin > 0 else 0
                }
                # Tự động gắn Hard TP/SL trực tiếp lên sàn BingX
                try:
                    state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", pos_side, pos_amt, sl_calc, tp1_calc)
                except Exception as e:
                    logger.warning(f"[SET TP/SL ERROR] {e}")
                logger.info(f"[LIVE SYNC] Phát hiện & đồng bộ vị thế từ BingX: {pos_side} {pos_amt} oz @ ${avg_price} (Đã gắn Hard TP: ${tp1_calc} & SL: ${sl_calc})")
            else:
                state.active_position["size"] = pos_amt
                state.active_position["margin"] = margin
                state.active_position["leverage"] = leverage

        # Đồng bộ và chống nhồi lệnh chờ Limit Maker từ sàn BingX
        ok_orders, open_orders, _ = state.bingx_client.get_open_orders("NCCOGOLD2USD-USDT")
        if ok_orders:
            limit_orders = [o for o in open_orders if o.get("type") == "LIMIT"]
            # Nếu có nhiều hơn 1 lệnh Limit chờ trên sàn -> Tự động hủy bớt chỉ giữ đúng 1 lệnh duy nhất
            if len(limit_orders) > 1:
                logger.warning(f"[ANTI-DUPLICATE] Phát hiện {len(limit_orders)} lệnh Limit trên BingX! Đang hủy các lệnh dư thừa...")
                for dup_ord in limit_orders[1:]:
                    dup_id = dup_ord.get("orderId")
                    if dup_id:
                        state.bingx_client.cancel_order("NCCOGOLD2USD-USDT", str(dup_id))
                limit_orders = [limit_orders[0]]

            if limit_orders and state.pending_limit_order is None and state.active_position is None:
                first_lim = limit_orders[0]
                lim_side = "LONG" if first_lim.get("side") == "BUY" else "SHORT"
                lim_price = float(first_lim.get("price", state.latest_price))
                lim_size = float(first_lim.get("origQty", 0.001))
                state.pending_limit_order = {
                    "order_id": first_lim.get("orderId"),
                    "side": lim_side,
                    "limit_price": lim_price,
                    "size": lim_size,
                    "margin": round((lim_price * lim_size) / state.leverage, 2),
                    "strategy": "LIMIT MAKER",
                    "placed_time": time.time(),
                    "placed_time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "candle_time": 0,
                    "ttl_seconds": 900
                }
                logger.info(f"[LIVE SYNC] Đồng bộ lệnh chờ Limit từ BingX: {lim_side} {lim_size} oz @ ${lim_price}")
            elif not limit_orders and state.pending_limit_order is not None and state.active_position is None:
                state.pending_limit_order = None
    except Exception as e:
        logger.warning(f"[SYNC LIVE POS ERROR] {e}")

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
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.close_all_positions("NCCOGOLD2USD-USDT")
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
            record_closed_trade_to_pnl(net, fee, "WIN", "CHỐT LỜI TP2 (+1.10%)", cur_p, f"{size:.3f} oz", state.balance)
            state.active_position = None
            logger.info(f"[LIVE TP2 LONG] Hit @ {cur_p} | Net: +${net:.2f} (BingX Maker Fee)")

        # 2. Chốt 75% ở TP1 (+0.48%) và Dời SL 25% còn lại vào vùng LÃI DƯƠNG (+0.12%)
        elif not tp1_hit and cur_p >= tp1:
            target_close = round(size * 0.75, 4)
            remaining_size = round(size - target_close, 4)
            
            # Nếu phần còn lại < 0.0005 oz (dưới min contract của sàn), chốt 100% toàn bộ vị thế
            if remaining_size < 0.0005:
                close_size = size
                is_full_close = True
            else:
                close_size = target_close
                is_full_close = False

            pos["size"] -= close_size
            pos["tp1_hit"] = True
            
            # Khóa lãi dương: entry + 25% khoảng cách TP1 (+0.12% lãi)
            lock_dist = (tp1 - entry) * 0.25
            new_sl = round(entry + lock_dist, 2)
            pos["sl"] = new_sl
            pos["stop_loss"] = new_sl

            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "SELL", "LONG", close_size)
                if not is_full_close and pos["size"] >= 0.0005:
                    state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", "LONG", pos["size"], sl_price=new_sl, tp_price=tp2)
                sync_live_balance()

            pnl = (cur_p - entry) * close_size
            fee = cur_p * close_size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            
            action_label = "CHỐT LỜI TP1 100% (+0.48%)" if is_full_close else "CHỐT LỜI TP1 75% (+0.48%)"
            state.trades.insert(0, {
                "time": now_str,
                "action": action_label + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            record_closed_trade_to_pnl(net, fee, "WIN", action_label, cur_p, f"{close_size:.3f} oz", state.balance)
            
            if is_full_close:
                state.active_position = None
                logger.info(f"[LIVE TP1 LONG FULL] Hit @ {cur_p} | Đã chốt 100% toàn bộ do size nhỏ")
            else:
                logger.info(f"[LIVE TP1 LONG 75%] Hit @ {cur_p} | Đã chốt {close_size:.3f} oz (75%) | Dời SL 25% còn lại ({pos['size']:.3f} oz) vào LÃI DƯƠNG: {new_sl} (+0.12%) & gắn lên BingX")

        # 3. KÍCH HOẠT EARLY BREAKEVEN: Khi lãi đạt >= +0.38% (gần chạm TP1) -> Dời SL về Entry hòa vốn bảo hiểm
        elif not tp1_hit and not pos.get("be_hit", False) and cur_p >= round(entry * (1.0 + state.quant_agent.be_trigger_pct), 2):
            pos["be_hit"] = True
            be_sl = round(entry * 1.0005, 2)  # Entry + 0.05% để bù đủ 2 lần phí giao dịch (Mở Maker 0.02% + Đóng Taker 0.05%)
            if be_sl > pos["sl"]:
                pos["sl"] = be_sl
                pos["stop_loss"] = be_sl
                if state.mode == "LIVE TRADING" and state.bingx_client:
                    state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", "LONG", pos["size"], sl_price=be_sl, tp_price=tp1)
                logger.info(f"[LIVE EARLY BREAKEVEN LONG] Giá đạt ${cur_p:.2f} (+0.38% - gần chạm TP1) -> Đã dời SL về HÒA VỐN BẢO HIỂM (+0.05% phí): ${be_sl:.2f}!")

        # 4. Chạm Cắt lỗ / Khóa lãi dương / Hòa vốn
        elif cur_p <= sl:
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.close_all_positions("NCCOGOLD2USD-USDT")
                sync_live_balance()
            pnl = (cur_p - entry) * size
            fee = cur_p * size * state.fee_rate
            net = pnl - fee
            state.balance += net
            
            if tp1_hit:
                act_type = "PROFIT_LOCK"
                act_text = "KHÓA LÃI DƯƠNG (+0.12%)"
            elif pos.get("be_hit", False):
                act_type = "BREAKEVEN"
                act_text = "HÒA VỐN BẢO HIỂM (EARLY BE)"
            else:
                act_type = "LOSS"
                act_text = "CẮT LỖ KỶ LUẬT (-0.25%)"
                state.quant_agent.daily_losses_count += 1
                if state.quant_agent.daily_losses_count >= state.quant_agent.max_daily_losses:
                    state.quant_agent.day_locked = True
                    logger.warning("[CIRCUIT BREAKER] Cầu dao tự ngắt: Dính 2 SL trong ngày!")
                save_bot_limits(state.quant_agent.daily_trades_count, state.quant_agent.daily_losses_count, state.quant_agent.day_locked)

            state.trades.insert(0, {
                "time": now_str,
                "action": act_text + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"{net:+.2f} USDT",
                "balance": f"${state.balance:.2f}",
                "type": act_type
            })
            record_closed_trade_to_pnl(net, fee, act_type, act_text, cur_p, f"{size:.3f} oz", state.balance)
            state.active_position = None
            logger.info(f"[LIVE EXIT LONG] Hit SL @ {cur_p} ({act_text})")

    elif side == "SHORT":
        # 1. Chốt lời toàn phần TP2 (+1.10%)
        if cur_p <= tp2:
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.close_all_positions("NCCOGOLD2USD-USDT")
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
            record_closed_trade_to_pnl(net, fee, "WIN", "CHỐT LỜI TP2 (+1.10%)", cur_p, f"{size:.3f} oz", state.balance)
            state.active_position = None
            logger.info(f"[LIVE TP2 SHORT] Hit @ {cur_p} | Net: +${net:.2f} (BingX Maker Fee)")

        # 2. Chốt 75% ở TP1 (+0.48%) và Dời SL 25% còn lại vào vùng LÃI DƯƠNG (+0.12%)
        elif not tp1_hit and cur_p <= tp1:
            target_close = round(size * 0.75, 4)
            remaining_size = round(size - target_close, 4)
            
            # Nếu phần còn lại < 0.0005 oz (dưới min contract của sàn), chốt 100% toàn bộ vị thế
            if remaining_size < 0.0005:
                close_size = size
                is_full_close = True
            else:
                close_size = target_close
                is_full_close = False

            pos["size"] -= close_size
            pos["tp1_hit"] = True
            
            # Khóa lãi dương: entry - 25% khoảng cách TP1 (+0.12% lãi)
            lock_dist = (entry - tp1) * 0.25
            new_sl = round(entry - lock_dist, 2)
            pos["sl"] = new_sl
            pos["stop_loss"] = new_sl

            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.place_market_order("NCCOGOLD2USD-USDT", "BUY", "SHORT", close_size)
                if not is_full_close and pos["size"] >= 0.0005:
                    state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", "SHORT", pos["size"], sl_price=new_sl, tp_price=tp2)
                sync_live_balance()

            pnl = (entry - cur_p) * close_size
            fee = cur_p * close_size * state.fee_rate_maker
            net = pnl - fee
            state.balance += net
            
            action_label = "CHỐT LỜI TP1 100% (+0.48%)" if is_full_close else "CHỐT LỜI TP1 75% (+0.48%)"
            state.trades.insert(0, {
                "time": now_str,
                "action": action_label + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{close_size:.3f} oz",
                "pnl": f"+${net:.2f}",
                "balance": f"${state.balance:.2f}",
                "type": "WIN"
            })
            record_closed_trade_to_pnl(net, fee, "WIN", action_label, cur_p, f"{close_size:.3f} oz", state.balance)
            
            if is_full_close:
                state.active_position = None
                logger.info(f"[LIVE TP1 SHORT FULL] Hit @ {cur_p} | Đã chốt 100% toàn bộ do size nhỏ")
            else:
                logger.info(f"[LIVE TP1 SHORT 75%] Hit @ {cur_p} | Đã chốt {close_size:.3f} oz (75%) | Dời SL 25% còn lại ({pos['size']:.3f} oz) vào LÃI DƯƠNG: {new_sl} (+0.12%) & gắn lên BingX")

        # 3. KÍCH HOẠT EARLY BREAKEVEN: Khi lãi đạt >= +0.38% (gần chạm TP1) -> Dời SL về Entry hòa vốn bảo hiểm
        elif not tp1_hit and not pos.get("be_hit", False) and cur_p <= round(entry * (1.0 - state.quant_agent.be_trigger_pct), 2):
            pos["be_hit"] = True
            be_sl = round(entry * 0.9995, 2)  # Entry - 0.05% để bù đủ 2 lần phí giao dịch (Mở Maker 0.02% + Đóng Taker 0.05%)
            if be_sl < pos["sl"]:
                pos["sl"] = be_sl
                pos["stop_loss"] = be_sl
                if state.mode == "LIVE TRADING" and state.bingx_client:
                    state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", "SHORT", pos["size"], sl_price=be_sl, tp_price=tp1)
                logger.info(f"[LIVE EARLY BREAKEVEN SHORT] Giá đạt ${cur_p:.2f} (+0.38% - gần chạm TP1) -> Đã dời SL về HÒA VỐN BẢO HIỂM (+0.05% phí): ${be_sl:.2f}!")

        # 4. Chạm Cắt lỗ / Khóa lãi dương / Hòa vốn
        elif cur_p >= sl:
            if state.mode == "LIVE TRADING" and state.bingx_client:
                state.bingx_client.cancel_all_open_orders("NCCOGOLD2USD-USDT")
                state.bingx_client.close_all_positions("NCCOGOLD2USD-USDT")
                sync_live_balance()
            pnl = (entry - cur_p) * size
            fee = cur_p * size * state.fee_rate
            net = pnl - fee
            state.balance += net
            
            if tp1_hit:
                act_type = "PROFIT_LOCK"
                act_text = "KHÓA LÃI DƯƠNG (+0.12%)"
            elif pos.get("be_hit", False):
                act_type = "BREAKEVEN"
                act_text = "HÒA VỐN BẢO HIỂM (EARLY BE)"
            else:
                act_type = "LOSS"
                act_text = "CẮT LỖ KỶ LUẬT (-0.25%)"
                state.quant_agent.daily_losses_count += 1
                if state.quant_agent.daily_losses_count >= state.quant_agent.max_daily_losses:
                    state.quant_agent.day_locked = True
                    logger.warning("[CIRCUIT BREAKER] Cầu dao tự ngắt: Dính 2 SL trong ngày!")
                save_bot_limits(state.quant_agent.daily_trades_count, state.quant_agent.daily_losses_count, state.quant_agent.day_locked)

            state.trades.insert(0, {
                "time": now_str,
                "action": act_text + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
                "price": cur_p,
                "size": f"{size:.3f} oz",
                "pnl": f"{net:+.2f} USDT",
                "balance": f"${state.balance:.2f}",
                "type": act_type
            })
            record_closed_trade_to_pnl(net, fee, act_type, act_text, cur_p, f"{size:.3f} oz", state.balance)
            state.active_position = None
            logger.info(f"[LIVE EXIT SHORT] Hit SL @ {cur_p} ({act_text})")

def check_and_manage_pending_limit_order():
    """
    Giám sát vòng đời Lệnh chờ Limit Maker:
    1. Kiểm tra hết hạn TTL (15 phút / 1 nến M15) -> Tự động HỦY LỆNH trên BingX.
    2. Kiểm tra gãy xu hướng EMA 300 (Invalidation) -> HỦY LỆNH khẩn cấp.
    3. Kiểm tra khớp lệnh (Filled) -> Chuyển thành Active Position + Cài TP1/TP2/SL tự động.
    """
    if not state.pending_limit_order:
        return

    cur_p = state.latest_price
    if cur_p <= 0:
        return

    order = state.pending_limit_order
    placed_t = order.get("placed_time", time.time())
    ttl = order.get("ttl_seconds", 900)
    side = order.get("side")
    limit_p = order.get("limit_price", 0.0)
    order_id = order.get("order_id")
    size = order.get("size", 0.001)
    margin = order.get("margin", 5.0)
    strategy = order.get("strategy", "SMA 20 PULLBACK")
    ema_val = state.indicators.get("ema_1h", cur_p)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. KIỂM TRA HẾT HẠN TTL HOẶC GÃY TREND EMA 300
    is_expired = (time.time() - placed_t) > ttl
    is_invalidated = (side == "LONG" and cur_p < ema_val) or (side == "SHORT" and cur_p > ema_val)

    if is_expired or is_invalidated:
        cancel_reason = "HẾT HẠN TTL 15 PHÚT (LỖI THỜI)" if is_expired else "GÃY XU HƯỚNG EMA 300"
        if state.mode == "LIVE TRADING" and state.bingx_client:
            if order_id:
                try:
                    state.bingx_client.cancel_order("NCCOGOLD2USD-USDT", str(order_id))
                except Exception as e:
                    logger.warning(f"[CANCEL ORDER ERROR] {e}")
            try:
                ok, open_orders, _ = state.bingx_client.get_open_orders("NCCOGOLD2USD-USDT")
                if ok and open_orders:
                    for o in open_orders:
                        if o.get("type") == "LIMIT" and o.get("orderId"):
                            logger.info(f"[SWEEP CANCEL EXPIRED LIMIT] Hủy lệnh LIMIT tồn đọng trên BingX: {o.get('orderId')}")
                            state.bingx_client.cancel_order("NCCOGOLD2USD-USDT", str(o.get("orderId")))
            except Exception as e:
                logger.warning(f"[SWEEP CANCEL ERROR] {e}")

        logger.info(f"[CANCEL PENDING LIMIT] Đã hủy lệnh chờ {side} @ ${limit_p:,.2f} | Lý do: {cancel_reason}")
        state.trades.insert(0, {
            "time": now_str,
            "action": f"HỦY LỆNH CHỜ {side} [{cancel_reason}]",
            "price": limit_p,
            "size": f"{size:.3f} oz",
            "pnl": "ĐÃ HỦY (0 PHÍ)",
            "balance": f"${state.balance:.2f}",
            "type": "CLOSE"
        })
        state.trades = state.trades[:100]
        state.pending_limit_order = None
        return

    # 2. KIỂM TRA KHỚP LỆNH (FILLED)
    is_filled = False
    if state.mode == "LIVE TRADING" and state.bingx_client and order_id:
        ok, res = state.bingx_client.get_order_status("NCCOGOLD2USD-USDT", str(order_id))
        if ok and str(res.get("status")) in ["FILLED", "2"]:
            is_filled = True
        elif side == "LONG" and cur_p <= limit_p:
            is_filled = True
        elif side == "SHORT" and cur_p >= limit_p:
            is_filled = True
    else:
        if side == "LONG" and cur_p <= limit_p:
            is_filled = True
        elif side == "SHORT" and cur_p >= limit_p:
            is_filled = True

    if is_filled:
        fee = limit_p * size * state.fee_rate_maker
        state.balance -= fee
        
        upper_bb = state.indicators.get("upper_bb", 0.0)
        lower_bb = state.indicators.get("lower_bb", 0.0)
        sl, tp1, tp2 = compute_front_run_tp_sl(side, limit_p, state.quant_agent.sl_pct, state.quant_agent.tp1_pct, state.quant_agent.tp2_pct, upper_bb, lower_bb)
        
        state.active_position = {
            "side": side,
            "entry_price": limit_p,
            "size": size,
            "margin": margin,
            "sl": sl,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "be_hit": False,
            "open_time": now_str,
            "strategy": strategy,
            "order_type": "LIMIT_MAKER",
            "unrealized_pnl": 0.0,
            "pnl_pct": 0.0
        }

        # Gắn Hard TP/SL trực tiếp lên sàn BingX
        if state.mode == "LIVE TRADING" and state.bingx_client:
            try:
                state.bingx_client.set_position_tp_sl("NCCOGOLD2USD-USDT", side, size, sl, tp1)
            except Exception as e:
                logger.warning(f"[ATTACH LIVE TP/SL ERROR] {e}")

        state.pending_limit_order = None
        state.quant_agent.daily_trades_count += 1
        if state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
            state.quant_agent.day_locked = True
        save_bot_limits(state.quant_agent.daily_trades_count, state.quant_agent.daily_losses_count, state.quant_agent.day_locked)
            
        state.trades.insert(0, {
            "time": now_str,
            "action": f"KHỚP LỆNH LIMIT {side} [{strategy}] (MAKER 0.02%)" + (" [BINGX LIVE]" if state.mode == "LIVE TRADING" else ""),
            "price": limit_p,
            "size": f"{size:.3f} oz",
            "pnl": f"-${fee:.2f} (Phí Maker)",
            "balance": f"${state.balance:.2f}",
            "type": "OPEN"
        })
        state.trades = state.trades[:100]
        logger.info(f"[LIMIT MAKER FILLED] {side} @ {limit_p:,.2f} | TP1: {tp1} | TP2: {tp2} | SL: {sl} (Đã gắn Hard TP/SL lên BingX)")

def check_live_entry_signal():
    # 1. Luôn tính toán cập nhật các chỉ báo kỹ thuật theo giá tick mới nhất
    if len(state.candles) >= 30:
        prices = [c["close"] for c in state.candles[:-1]] + [state.latest_price]
        upper_bb, lower_bb, rsi, ema_1h, rsi_fast = state.quant_agent.calculate_indicators(prices)
        state.indicators = {
            "upper_bb": round(upper_bb, 2),
            "lower_bb": round(lower_bb, 2),
            "sma_bb": round((upper_bb + lower_bb) / 2, 2),
            "ema_1h": round(ema_1h, 2),
            "rsi": round(rsi, 1),
            "rsi_fast": round(rsi_fast, 1)
        }

    # 2. Kiểm tra các bộ lọc sinh mệnh và kỷ luật: KHÓA NHỒI LỆNH (Tuyệt đối không nhồi khi đã có lệnh active hoặc limit chờ)
    if state.active_position is not None or state.pending_limit_order is not None:
        return

    # Double-check trực tiếp sàn BingX ở chế độ LIVE TRADING: Tuyệt đối không đặt thêm nếu sàn vẫn còn lệnh LIMIT chờ
    if state.mode == "LIVE TRADING" and state.bingx_client:
        try:
            ok, open_orders, _ = state.bingx_client.get_open_orders("NCCOGOLD2USD-USDT")
            if ok and open_orders:
                has_live_limit = any(o.get("type") == "LIMIT" for o in open_orders)
                if has_live_limit:
                    return
        except Exception as e:
            logger.warning(f"[CHECK LIVE OPEN ORDERS ERROR] {e}")

    if state.quant_agent.day_locked or state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
        return
    if len(state.candles) < 30:
        return

    cur_candle = state.candles[-1]
    prev_candle = state.candles[-2] if len(state.candles) >= 2 else cur_candle
    cur_time = cur_candle.get("time", 0)

    # KHÓA KỶ LUẬT: Tuyệt đối không mở quá 1 lệnh trên cùng 1 cây nến 15 phút!
    if state.last_trade_candle_time == cur_time:
        return

    now_vn = datetime.now()
    hour = now_vn.hour
    minute = now_vn.minute
    time_float = hour + minute / 60.0

    # 1. BỘ LỌC CỬA SỔ GIỜ VÀNG (Golden Liquidity Windows):
    # - Loại bỏ 100% bẫy thanh khoản sáng phiên Á & giãn spread (04:00 - 08:59 VN)
    if state.quant_agent.filter_asia_morning and (4.0 <= time_float < 9.0):
        return

    is_london_golden = (13.5 <= time_float <= 18.5)  # 13:30 - 18:30 VN
    is_ny_golden = (19.5 <= time_float <= 23.5)      # 19:30 - 23:30 VN
    is_golden_session = is_london_golden or is_ny_golden

    close_p = state.latest_price
    ema_1h = state.indicators["ema_1h"]
    upper_bb = state.indicators["upper_bb"]
    lower_bb = state.indicators["lower_bb"]
    sma_bb = state.indicators.get("sma_bb", (upper_bb + lower_bb) / 2.0)
    rsi = state.indicators["rsi"]
    rsi_fast = state.indicators.get("rsi_fast", rsi)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 2. BỘ LỌC KHỐI LƯỢNG DÒNG TIỀN (Volume Spread Analysis - VSA):
    vols = [float(c.get("volume", c.get("amount", 0.0))) for c in state.candles[-20:] if float(c.get("volume", c.get("amount", 0.0))) > 0]
    avg_vol = (sum(vols) / len(vols)) if len(vols) >= 5 else 0.0
    prev_vol = float(prev_candle.get("volume", prev_candle.get("amount", 0.0)))
    
    # 3. BỘ LỌC NẾN RÚT RÂU / TỪ CHỐI GIÁ (Price Rejection Wick):
    c_open = float(cur_candle.get("open", close_p))
    c_high = float(cur_candle.get("high", close_p))
    c_low = float(cur_candle.get("low", close_p))
    c_range = max(0.01, c_high - c_low)

    prev_open = float(prev_candle.get("open", close_p))
    prev_high = float(prev_candle.get("high", close_p))
    prev_low = float(prev_candle.get("low", close_p))
    prev_range = max(0.01, prev_high - prev_low)

    # Râu dưới (Lower Wick) cho lệnh BUY
    cur_lower_wick_ratio = (min(c_open, close_p) - c_low) / c_range
    prev_lower_wick_ratio = (min(prev_open, float(prev_candle.get("close", close_p))) - prev_low) / prev_range
    has_buy_rejection = (cur_lower_wick_ratio >= 0.15 or prev_lower_wick_ratio >= 0.20 or is_golden_session)

    # Râu trên (Upper Wick) cho lệnh SELL
    cur_upper_wick_ratio = (c_high - max(c_open, close_p)) / c_range
    prev_upper_wick_ratio = (prev_high - max(prev_open, float(prev_candle.get("close", close_p)))) / prev_range
    has_sell_rejection = (cur_upper_wick_ratio >= 0.15 or prev_upper_wick_ratio >= 0.20 or is_golden_session)

    # Chống bắt dao rơi nếu có nến xả / bơm đột biến ngược chiều (Anti-Flush > 2.5x avg vol)
    is_anti_flush_buy = True
    if avg_vol > 0 and prev_vol > 2.5 * avg_vol:
        if (prev_open - float(prev_candle.get("close", close_p))) / prev_open > 0.005:
            is_anti_flush_buy = False  # Chặn bắt dao rơi khi xả mạnh

    is_anti_flush_sell = True
    if avg_vol > 0 and prev_vol > 2.5 * avg_vol:
        if (float(prev_candle.get("close", close_p)) - prev_open) / prev_open > 0.005:
            is_anti_flush_sell = False  # Chặn bắt đỉnh khi bơm mạnh

    # 4. ĐỒNG PHA ĐA KHUNG THỜI GIAN (M5 / M15 Momentum):
    is_long_momentum = (rsi_fast <= 45.0 or rsi_fast >= rsi)
    is_short_momentum = (rsi_fast >= 55.0 or rsi_fast <= rsi)

    # ================== 1. TÍN HIỆU LONG ==================
    is_long_setup_sma = (close_p > ema_1h and close_p <= sma_bb * 1.006 and rsi <= (54.0 if is_golden_session else 50.0))
    is_long_setup_deep = (close_p > ema_1h and close_p <= lower_bb * 1.004 and rsi <= 48.0)

    if (not state.active_position) and (not state.pending_limit_order) and (is_long_setup_sma or is_long_setup_deep):
        if is_anti_flush_buy and has_buy_rejection and is_long_momentum:
            strategy_tag = "DEEP DIP" if is_long_setup_deep else "SMA 20 PULLBACK"
            if is_golden_session:
                strategy_tag += " [GOLDEN A++]"
            limit_p = round(lower_bb if is_long_setup_deep else sma_bb, 2)
            if limit_p > close_p:
                limit_p = round(close_p, 2)

            size, margin, est_risk = compute_smart_order_sizing(
                balance=state.balance,
                close_p=limit_p,
                leverage=state.leverage,
                sl_pct=state.quant_agent.sl_pct,
                risk_pct=state.risk_per_trade_pct,
                max_margin_pct=state.max_margin_pct
            )

            order_id = None
            if state.mode == "LIVE TRADING":
                if not state.bingx_client:
                    logger.error("[LIVE ERROR] Chưa có BingX Client để mở lệnh Live!")
                    return
                if state.balance < 2.0:
                    logger.warning(f"[LIVE INSUFFICIENT BALANCE] Số dư ${state.balance:.2f} < 2 USDT tối thiểu!")
                    return
                ok, res = state.bingx_client.place_limit_order("NCCOGOLD2USD-USDT", "BUY", "LONG", size, limit_p)
                if not ok:
                    logger.error(f"[LIVE LIMIT ORDER FAILED] Không thể đặt lệnh BUY LIMIT BingX: {res}")
                    return
                order_id = res.get("orderId") or (res.get("order", {}).get("orderId") if isinstance(res.get("order"), dict) else None)

            state.pending_limit_order = {
                "order_id": order_id,
                "side": "LONG",
                "limit_price": limit_p,
                "size": size,
                "margin": margin,
                "strategy": strategy_tag,
                "placed_time": time.time(),
                "placed_time_str": now_str,
                "candle_time": cur_time,
                "ttl_seconds": 900
            }
            state.last_trade_candle_time = cur_time
            logger.info(f"[PLACED BUY LIMIT ({strategy_tag})] @ {limit_p:,.2f} | Size: {size:.3f} oz | TTL: 15 mins (A++ Confirmed)")

    # ================== 2. TÍN HIỆU SHORT ==================
    is_short_setup_sma = (close_p < ema_1h and close_p >= sma_bb * 0.994 and rsi >= (46.0 if is_golden_session else 50.0))
    is_short_setup_deep = (close_p < ema_1h and close_p >= upper_bb * 0.996 and rsi >= 52.0)

    if (not state.active_position) and (not state.pending_limit_order) and (is_short_setup_sma or is_short_setup_deep):
        if is_anti_flush_sell and has_sell_rejection and is_short_momentum:
            strategy_tag = "DEEP PEAK" if is_short_setup_deep else "SMA 20 PULLBACK"
            if is_golden_session:
                strategy_tag += " [GOLDEN A++]"
            limit_p = round(upper_bb if is_short_setup_deep else sma_bb, 2)
            if limit_p < close_p:
                limit_p = round(close_p, 2)

            size, margin, est_risk = compute_smart_order_sizing(
                balance=state.balance,
                close_p=limit_p,
                leverage=state.leverage,
                sl_pct=state.quant_agent.sl_pct,
                risk_pct=state.risk_per_trade_pct,
                max_margin_pct=state.max_margin_pct
            )

            order_id = None
            if state.mode == "LIVE TRADING":
                if not state.bingx_client:
                    logger.error("[LIVE ERROR] Chưa có BingX Client để mở lệnh Live!")
                    return
                if state.balance < 2.0:
                    logger.warning(f"[LIVE INSUFFICIENT BALANCE] Số dư ${state.balance:.2f} < 2 USDT tối thiểu!")
                    return
                ok, res = state.bingx_client.place_limit_order("NCCOGOLD2USD-USDT", "SELL", "SHORT", size, limit_p)
                if not ok:
                    logger.error(f"[LIVE LIMIT ORDER FAILED] Không thể đặt lệnh SELL LIMIT BingX: {res}")
                    return
                order_id = res.get("orderId") or (res.get("order", {}).get("orderId") if isinstance(res.get("order"), dict) else None)

            state.pending_limit_order = {
                "order_id": order_id,
                "side": "SHORT",
                "limit_price": limit_p,
                "size": size,
                "margin": margin,
                "strategy": strategy_tag,
                "placed_time": time.time(),
                "placed_time_str": now_str,
                "candle_time": cur_time,
                "ttl_seconds": 900
            }
            state.last_trade_candle_time = cur_time
            logger.info(f"[PLACED SELL LIMIT ({strategy_tag})] @ {limit_p:,.2f} | Size: {size:.3f} oz | TTL: 15 mins (A++ Confirmed)")

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
                                
                            check_and_manage_pending_limit_order()
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

                # 2. Định kỳ đồng bộ vị thế và số dư ví BingX thật mỗi 5 giây nếu đang ở chế độ LIVE
                if state.mode == "LIVE TRADING" and state.bingx_client:
                    if now_t - state.last_balance_sync > 5:
                        await asyncio.to_thread(sync_live_positions_with_bingx)
                        state.last_balance_sync = now_t

                # 3. Quản lý lệnh chờ Limit Maker (TTL & Khớp lệnh)
                check_and_manage_pending_limit_order()

                # 4. Quét tín hiệu mở lệnh mới khi đủ điều kiện
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

    # 3.5. ĐANG TREO LỆNH CHỜ LIMIT MAKER (TTL 15 PHÚT)
    if state.pending_limit_order is not None:
        p_ord = state.pending_limit_order
        p_side = p_ord.get("side", "LONG")
        p_price = p_ord.get("limit_price", 0.0)
        p_strat = p_ord.get("strategy", "SMA 20 PULLBACK")
        p_time = p_ord.get("placed_time", time.time())
        p_ttl = p_ord.get("ttl_seconds", 900)
        elapsed = int(time.time() - p_time)
        remaining = max(0, p_ttl - elapsed)
        rem_min = remaining // 60
        rem_sec = remaining % 60
        dist = abs(state.latest_price - p_price)
        return {
            "code": "PENDING_LIMIT_ORDER",
            "status_text": f"ĐANG TREO LỆNH LIMIT {p_side} @ ${p_price:,.2f}",
            "badge_color": "#0ecb81",
            "icon": "fa-clock",
            "detail_text": f"Đã đặt lệnh Limit Maker ({p_strat}) tại ${p_price:,.2f} (cách ${dist:,.1f}). Phí Maker 0.02% (Tiết kiệm 60%). Tự động hủy sau {rem_min:02d}:{rem_sec:02d} nếu chưa khớp."
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

    # 5. Hạn mức lệnh ngày (Daily trade limit - tối đa 8 lệnh)
    if state.quant_agent.daily_trades_count >= state.quant_agent.max_daily_trades:
        return {
            "code": "DAILY_LIMIT_REACHED",
            "status_text": "ĐẠT HẠN MỨC NGÀY (8/8 LỆNH)",
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

    # 9. Đang rình mồi thiết lập Dual A+ (Stalking Dual A+ Setup)
    p = state.latest_price
    ema = state.indicators.get("ema_1h", p)
    upper_bb = state.indicators.get("upper_bb", p)
    lower_bb = state.indicators.get("lower_bb", p)
    sma_bb = state.indicators.get("sma_bb", (upper_bb + lower_bb) / 2.0)
    rsi = state.indicators.get("rsi", 50.0)

    is_long_trend = p > ema
    missing_conds = []

    if is_long_trend:
        trend_name = "LONG"
        dist_sma = max(0.0, p - sma_bb)
        dist_lower = max(0.0, p - lower_bb)
        if p > sma_bb * 1.001:
            missing_conds.append(f"Chờ hồi SMA 20 (${sma_bb:,.1f}, cách ${dist_sma:,.1f}) hoặc BB Dưới (${lower_bb:,.1f})")
        if rsi > 52.0:
            missing_conds.append(f"RSI đang {rsi:.1f} (cần nguội về <= 52.0)")
    else:
        trend_name = "SHORT"
        dist_sma = max(0.0, sma_bb - p)
        dist_upper = max(0.0, upper_bb - p)
        if p < sma_bb * 0.999:
            missing_conds.append(f"Chờ hồi SMA 20 (${sma_bb:,.1f}, cách ${dist_sma:,.1f}) hoặc BB Trên (${upper_bb:,.1f})")
        if rsi < 48.0:
            missing_conds.append(f"RSI đang {rsi:.1f} (cần hồi lên >= 48.0)")

    if missing_conds:
        reasons_str = " | ".join(missing_conds)
        return {
            "code": "WAITING_SETUP",
            "status_text": f"ĐANG RÌNH MỒI DUAL A+ (ƯU TIÊN {trend_name})",
            "badge_color": "#f0ad4e",
            "icon": "fa-hourglass-half",
            "detail_text": f"Sóng lớn {trend_name} (Giá {'>' if is_long_trend else '<'} EMA 300). Đang đợi: {reasons_str}. AI Dual A+ sẵn sàng bóp cò!"
        }
    else:
        return {
            "code": "FIRING",
            "status_text": f"HỘI TỤ TÍN HIỆU DUAL A+ {trend_name} - KHAI HỎA!",
            "badge_color": "#0ecb81",
            "icon": "fa-bolt",
            "detail_text": f"Tín hiệu Dual A+ {trend_name} (SMA 20 Pullback / Deep Reversal) đã hội tụ đầy đủ. Hệ thống đang tiến hành mở lệnh!"
        }

def get_full_state_payload() -> Dict[str, Any]:
    init_bal = state.initial_balance if (state.initial_balance and state.initial_balance > 0) else 1000.0
    pnl_net = state.equity - init_bal
    roi_pct = (pnl_net / init_bal) * 100
    wait_reason = compute_wait_reason()
    
    cur_p = state.latest_price if state.latest_price > 0 else 4145.0
    size_est, margin_est, risk_est = compute_smart_order_sizing(
        balance=state.balance,
        close_p=cur_p,
        leverage=state.leverage,
        sl_pct=state.quant_agent.sl_pct,
        risk_pct=state.risk_per_trade_pct,
        max_margin_pct=state.max_margin_pct
    )
    
    upper_bb = state.indicators.get("upper_bb", cur_p)
    lower_bb = state.indicators.get("lower_bb", cur_p)
    sma_bb = state.indicators.get("sma_bb", (upper_bb + lower_bb) / 2.0)
    rsi_val = state.indicators.get("rsi", 50.0)
    ema_val = state.indicators.get("ema_1h", cur_p)

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
        "pending_limit_order": state.pending_limit_order,
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
        "leverage": int(state.leverage),
        "margin_pct": round(state.margin_pct * 100, 1),
        "capital_mgmt": {
            "leverage": int(state.leverage),
            "risk_per_trade_pct": round(state.risk_per_trade_pct * 100, 1),
            "est_margin": round(margin_est, 2),
            "est_size": round(size_est, 4),
            "est_max_loss": round(risk_est, 2),
            "free_margin_buffer": round(max(0.0, state.balance - margin_est), 2),
            "free_margin_ratio": round(((state.balance - margin_est) / max(0.01, state.balance)) * 100, 1),
            "status_text": f"🛡️ BẢO VỆ CHỐNG CHÁY ({state.risk_per_trade_pct*100:.1f}% Risk/lệnh)"
        },
        "active_position": state.active_position,
        "indicators": state.indicators,
        "trades": state.trades[:15],
        "candles": state.candles[-160:],
        "radar": {
            "heartbeat": datetime.now().strftime("%H:%M:%S"),
            "wait_reason": wait_reason,
            "cond_trend_long": cur_p > ema_val,
            "cond_bb_long": cur_p <= sma_bb * 1.001,
            "cond_rsi_long": rsi_val <= 52.0,
            "cond_trend_short": cur_p < ema_val,
            "cond_bb_short": cur_p >= sma_bb * 0.999,
            "cond_rsi_short": rsi_val >= 48.0,
            "daily_trades": state.quant_agent.daily_trades_count,
            "max_trades": state.quant_agent.max_daily_trades,
            "daily_losses": state.quant_agent.daily_losses_count,
            "max_losses": state.quant_agent.max_daily_losses,
            "day_locked": state.quant_agent.day_locked
        }
    }

@app.post("/api/cancel_pending_limit")
async def api_cancel_pending_limit():
    if not state.pending_limit_order:
        return {"status": "error", "message": "Không có lệnh chờ Limit nào đang hoạt động"}
    
    order = state.pending_limit_order
    order_id = order.get("order_id")
    side = order.get("side")
    limit_p = order.get("limit_price", 0.0)
    
    if state.mode == "LIVE TRADING" and state.bingx_client:
        if order_id:
            try:
                state.bingx_client.cancel_order("NCCOGOLD2USD-USDT", str(order_id))
            except Exception as e:
                logger.warning(f"[MANUAL CANCEL ORDER ERROR] {e}")
        try:
            ok, open_orders, _ = state.bingx_client.get_open_orders("NCCOGOLD2USD-USDT")
            if ok and open_orders:
                for o in open_orders:
                    if o.get("type") == "LIMIT" and o.get("orderId"):
                        logger.info(f"[MANUAL SWEEP CANCEL] Hủy lệnh LIMIT tồn đọng trên BingX: {o.get('orderId')}")
                        state.bingx_client.cancel_order("NCCOGOLD2USD-USDT", str(o.get("orderId")))
        except Exception as e:
            logger.warning(f"[MANUAL SWEEP CANCEL ERROR] {e}")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.trades.insert(0, {
        "time": now_str,
        "action": f"HỦY LỆNH CHỜ {side} [THỦ CÔNG]",
        "price": limit_p,
        "size": f"{order.get('size', 0.001):.3f} oz",
        "pnl": "ĐÃ HỦY (0 PHÍ)",
        "balance": f"${state.balance:.2f}",
        "type": "CLOSE"
    })
    state.trades = state.trades[:100]
    state.pending_limit_order = None
    logger.info(f"[MANUAL CANCEL PENDING LIMIT] Người dùng đã hủy lệnh chờ {side} @ ${limit_p:,.2f}")
    return {"status": "success", "message": f"Đã hủy lệnh chờ Limit {side} thành công"}

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
async def api_control(req: ControlRequest):
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
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass
    return {"status": "success", "action": req.action}

class ResetAccountRequest(BaseModel):
    balance: Optional[float] = 1000.0

@app.post("/api/reset_account")
async def api_reset_account(req: Optional[ResetAccountRequest] = None):
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
    save_bot_limits(0, 0, False)
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass
    return {"status": "success", "message": f"Đã đặt lại tài khoản Demo về ${initial:,.2f}"}

@app.post("/api/reset_limits")
async def api_reset_limits():
    state.quant_agent.daily_trades_count = 0
    state.quant_agent.daily_losses_count = 0
    state.quant_agent.day_locked = False
    state.last_trade_candle_time = 0
    state.trades = []
    save_bot_limits(0, 0, False)
    
    # Đồng bộ reset ngày hôm nay trong daily_pnl.json
    try:
        from daily_pnl_tracker import load_pnl_data, save_pnl_data
        today_s = datetime.now().strftime("%Y-%m-%d")
        pnl_db = load_pnl_data()
        if today_s in pnl_db:
            pnl_db[today_s]["trades_count"] = 0
            pnl_db[today_s]["wins"] = 0
            pnl_db[today_s]["losses"] = 0
            pnl_db[today_s]["trades"] = []
            save_pnl_data(pnl_db)
    except Exception:
        pass

    logger.info("[RESET] Đã reset về 0 Giới hạn Sniper và lưu trữ bền vững!")
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass
    return {"status": "success", "message": "Đã reset về 0 Giới hạn Sniper (0/8 Lệnh, 0/2 SL) & lưu trữ thành công!"}

class TestTradeRequest(BaseModel):
    side: str                          # "LONG" hoặc "SHORT"
    leverage: Optional[float] = 16.0   # Đòn bẩy tùy chọn (1x - 50x)
    margin_pct: Optional[float] = None # Ký quỹ % (0.05 - 1.0)
    margin_usdt: Optional[float] = None# Ký quỹ USDT cố định

@app.post("/api/test_trade")
async def api_test_trade(req: TestTradeRequest):
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
    
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass

    return JSONResponse({
        "status": "success",
        "message": f"Đã mở thành công lệnh THỬ NGHIỆM {side} tại giá ${close_p:,.2f}! Quan sát lệnh đang chạy ngay tại khung Vị Thế.",
        "position": state.active_position
    })

@app.post("/api/close_position")
async def api_close_position():
    if not state.active_position:
        if state.mode == "LIVE TRADING" and state.bingx_client:
            ok, res = await asyncio.to_thread(state.bingx_client.close_all_positions, "NCCOGOLD2USD-USDT")
            await asyncio.to_thread(sync_live_balance)
            return JSONResponse({
                "status": "success" if ok else "error",
                "message": "Đã gửi lệnh đóng toàn bộ vị thế trên sàn BingX thành công!" if ok else f"Lỗi đóng vị thế BingX: {res}",
                "balance": state.balance
            })
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
        # 1. Hủy toàn bộ lệnh chờ/TP/SL cũ và Đóng trực tiếp toàn bộ vị thế trên sàn BingX
        await asyncio.to_thread(state.bingx_client.cancel_all_open_orders, "NCCOGOLD2USD-USDT")
        ok_close, close_res = await asyncio.to_thread(state.bingx_client.close_all_positions, "NCCOGOLD2USD-USDT")
        if not ok_close:
            order_side = "SELL" if side == "LONG" else "BUY"
            await asyncio.to_thread(state.bingx_client.place_market_order, "NCCOGOLD2USD-USDT", order_side, side, size)
        await asyncio.to_thread(sync_live_balance)
        
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
    record_closed_trade_to_pnl(net, fee, trade_type, act_name, cur_p, f"{size:.3f} oz", state.balance)
    state.active_position = None
    logger.info(f"[MANUAL CLOSE] {side} closed @ {cur_p} | Net: {net:+.2f} USDT (Đã đóng trên BingX)")
    
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass

    return JSONResponse({
        "status": "success",
        "message": f"Đã đóng vị thế thị trường và đồng bộ sàn BingX thành công tại giá ${cur_p:,.2f}! PnL ròng: {net:+.2f} USDT.",
        "balance": state.balance
    })

class SetModeRequest(BaseModel):
    mode: str                          # "LIVE" hoặc "DEMO"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

@app.post("/api/set_mode")
async def api_set_mode(req: SetModeRequest):
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
            
        client.set_leverage(symbol="NCCOGOLD2USD-USDT", leverage=int(state.leverage))
        
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
        try:
            await manager.broadcast(get_full_state_payload())
        except Exception:
            pass
            
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
        
        try:
            await manager.broadcast(get_full_state_payload())
        except Exception:
            pass
            
        return JSONResponse({
            "status": "success",
            "mode": "DEMO (Paper Trading)",
            "balance": 1000.0,
            "message": "Đã chuyển về chế độ DEMO (Paper Trading) an toàn với số dư $1,000."
        })

@app.post("/api/sync_balance")
async def api_sync_balance():
    if state.mode != "LIVE TRADING" or not state.bingx_client:
        return JSONResponse({
            "status": "not_live",
            "balance": state.balance,
            "equity": state.equity,
            "message": "Đang ở chế độ Demo, số dư giả lập tĩnh."
        })
    ok, bal, eq, msg = sync_live_balance()
    if ok:
        try:
            await manager.broadcast(get_full_state_payload())
        except Exception:
            pass
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

class SetLeverageRequest(BaseModel):
    leverage: int
    risk_pct: Optional[float] = None

@app.post("/api/set_leverage")
async def api_set_leverage(req: SetLeverageRequest):
    lev = int(req.leverage)
    if lev < 1 or lev > 500:
        return JSONResponse({
            "status": "error",
            "message": "Đòn bẩy không hợp lệ! Vui lòng chọn trong khoảng từ 1x đến 500x."
        }, status_code=400)
    
    state.leverage = float(lev)
    if req.risk_pct is not None and 0.005 <= req.risk_pct <= 0.10:
        state.risk_per_trade_pct = float(req.risk_pct)
        
    msg_bingx = ""
    if state.bingx_client and state.bingx_client.api_key:
        ok, msg = state.bingx_client.set_leverage("NCCOGOLD2USD-USDT", lev)
        if ok:
            msg_bingx = f" và đồng bộ thành công lên sàn BingX ({lev}x)"
        else:
            msg_bingx = f" (cảnh báo sàn: {msg})"
            
    logger.info(f"[LEVERAGE CHANGED] Đòn bẩy mới: {lev}x | Risk/lệnh: {state.risk_per_trade_pct*100:.1f}%{msg_bingx}")
    try:
        await manager.broadcast(get_full_state_payload())
    except Exception:
        pass
        
    return JSONResponse({
        "status": "success",
        "leverage": lev,
        "risk_pct": state.risk_per_trade_pct,
        "message": f"Đã áp dụng đòn bẩy {lev}x{msg_bingx}!"
    })

@app.get("/api/leverage_info")
def api_leverage_info():
    cur_p = state.latest_price if state.latest_price > 0 else 4145.0
    size_est, margin_est, risk_est = compute_smart_order_sizing(
        balance=state.balance,
        close_p=cur_p,
        leverage=state.leverage,
        sl_pct=state.quant_agent.sl_pct,
        risk_pct=state.risk_per_trade_pct,
        max_margin_pct=state.max_margin_pct
    )
    return JSONResponse({
        "current_leverage": int(state.leverage),
        "supported_presets": [10, 16, 25, 50, 100, 200, 500],
        "max_leverage": 500,
        "risk_per_trade_pct": round(state.risk_per_trade_pct * 100, 1),
        "est_margin": round(margin_est, 2),
        "est_size": round(size_est, 4),
        "est_max_loss": round(risk_est, 2),
        "free_margin_buffer": round(max(0.0, state.balance - margin_est), 2),
        "free_margin_ratio": round(((state.balance - margin_est) / max(0.01, state.balance)) * 100, 1),
        "symbol": "NCCOGOLD2USD-USDT"
    })

@app.get("/api/monthly_pnl")
def api_monthly_pnl(month: Optional[str] = None):
    return JSONResponse(pnl_tracker.get_monthly_data(month))

INTERVAL_MAP = {
    "Min1": "1m", "Min5": "5m", "Min15": "15m", "Min30": "30m",
    "Min60": "1h", "Hour4": "4h", "Day1": "1d",
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"
}

@app.get("/api/kline")
def api_kline(interval: str = "Min15", limit: int = 200):
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
