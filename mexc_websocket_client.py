"""
mexc_websocket_client.py
------------------------
Module kết nối WebSocket thời gian thực tới MEXC Futures.
Chịu trách nhiệm:
- Duy trì kết nối WebSocket và gửi heartbeat ping/pong.
- Đăng ký nhận luồng K-line (nến) cho cặp giao dịch chỉ định.
- Trích xuất dữ liệu OHLCV và đẩy vào asyncio.Queue.
"""

import json
import asyncio
import logging
import websockets
from typing import Dict, Any

logger = logging.getLogger("MEXCWebSocket")


class MEXCWebSocketClient:
    """
    Client WebSocket bất đồng bộ kết nối tới MEXC Futures K-line stream.
    """

    WS_URL = "wss://contract.mexc.com/edge"

    def __init__(self, symbol: str = "XAU_USDT", interval: str = "Min1", queue: asyncio.Queue = None):
        self.symbol = symbol
        self.interval = interval
        self.queue = queue or asyncio.Queue()
        self.is_running = True
        self._ws = None

    async def connect_and_listen(self):
        """
        Khởi tạo kết nối WebSocket, gửi subscription và lắng nghe stream dữ liệu.
        Tự động gửi ping định kỳ để giữ kết nối sống.
        """
        while self.is_running:
            try:
                logger.info(f"[WS CONNECTING] Đang kết nối tới MEXC Futures WS: {self.WS_URL} ({self.symbol})")
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=None, # Tự quản lý heartbeat theo giao thức MEXC
                    close_timeout=10
                ) as ws:
                    self._ws = ws
                    logger.info(f"[WS CONNECTED] Đã kết nối thành công tới MEXC Futures WS.")

                    # Gửi tin nhắn đăng ký nhận K-line
                    # Format MEXC Futures Contract WS: {"method":"sub.kline", "param":{"symbol":"BTC_USDT","interval":"Min1"}}
                    sub_message = {
                        "method": "sub.kline",
                        "param": {
                            "symbol": self.symbol,
                            "interval": self.interval
                        }
                    }
                    await ws.send(json.dumps(sub_message))
                    logger.info(f"[WS SUBSCRIBED] Đã đăng ký kênh K-line: {self.symbol} - {self.interval}")

                    # Chạy song song tác vụ gửi Ping định kỳ và nhận Message
                    ping_task = asyncio.create_task(self._keep_alive_ping(ws))
                    listen_task = asyncio.create_task(self._listen_loop(ws))

                    done, pending = await asyncio.wait(
                        [ping_task, listen_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()

            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                if not self.is_running:
                    break
                logger.warning(f"[WS DISCONNECTED] Mất kết nối WebSocket: {e}. Thử kết nối lại sau 3 giây...")
                await asyncio.sleep(3)

    async def _keep_alive_ping(self, ws):
        """
        Gửi gói tin ping định kỳ (15 giây) theo giao thức MEXC Futures.
        """
        while self.is_running and ws.open:
            try:
                await asyncio.sleep(15)
                ping_msg = {"method": "ping"}
                await ws.send(json.dumps(ping_msg))
                logger.debug("[WS PING] Sent keepalive ping")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[WS PING ERROR] Lỗi gửi ping: {e}")
                break

    async def _listen_loop(self, ws):
        """
        Lắng nghe và phân giải dữ liệu K-line từ MEXC Futures.
        """
        async for message in ws:
            if not self.is_running:
                break

            try:
                # Xử lý phản hồi Pong
                if message == "pong" or '"channel":"pong"' in message or '"data":"pong"' in message:
                    logger.debug("[WS PONG] Received pong response")
                    continue

                data = json.loads(message)

                # Kiểm tra channel K-line
                # Cấu trúc MEXC Contract Kline data: {"channel":"push.kline","data":{"symbol":"BTC_USDT","interval":"Min1","t":167...,"o":...,"h":...,"l":...,"c":...,"v":...},"symbol":"BTC_USDT","ts":...}
                channel = data.get("channel", "")
                if "kline" in channel and "data" in data:
                    kline_info = data["data"]
                    
                    parsed_candle = {
                        "symbol": kline_info.get("symbol", self.symbol),
                        "interval": kline_info.get("interval", self.interval),
                        "timestamp": kline_info.get("t") or data.get("ts"),
                        "open": float(kline_info.get("o", 0.0)),
                        "high": float(kline_info.get("h", 0.0)),
                        "low": float(kline_info.get("l", 0.0)),
                        "close": float(kline_info.get("c", 0.0)),
                        "volume": float(kline_info.get("v", 0.0) or kline_info.get("a", 0.0))
                    }

                    # Đẩy vào queue để Quant Agent xử lý
                    await self.queue.put(parsed_candle)
                    logger.debug(f"[WS DATA QUEUED] {parsed_candle['symbol']} Close: {parsed_candle['close']}")

            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"[WS PARSE ERROR] Lỗi xử lý message: {e}")

    def stop(self):
        """Dừng client."""
        self.is_running = False
