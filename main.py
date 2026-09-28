"""
main.py
-------
Nhạc trưởng điều phối Hệ thống AI Agent Giao Dịch MEXC Futures (Survival Entity).
Chịu trách nhiệm:
1. Khởi tạo SurvivalCore (ExecutionRiskAgent) với API Keys từ biến môi trường.
2. Quản lý tác vụ chạy nền kiểm tra chỉ số sinh tồn (Vital Signs Monitor) mỗi 5 giây.
3. Điều phối luồng dữ liệu WebSocket K-line -> Ingestion Queue.
4. Lên lịch cập nhật Người Gác Cổng Vĩ Mô (Macro Sentiment Gatekeeper) định kỳ.
5. Tiếp nhận tín hiệu Quant và chuyển tiếp qua Lõi Sinh Tồn để kiểm duyệt & bóp cò.
6. Ghi log chuẩn định dạng: [ALIVE], [DEAD], [GATEKEEPER BLOCKED].
"""

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from execution_risk_agent import ExecutionRiskAgent
from mexc_websocket_client import MEXCWebSocketClient
from quant_pricing_agent import QuantPricingAgent
from macro_sentiment_agent import MacroSentimentAgent

# Nạp biến môi trường từ .env nếu có
load_dotenv()

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SurvivalOrchestrator")


class SurvivalTradingSystem:
    """
    Hệ thống điều phối toàn bộ các Agent thành một 'Thực thể Sinh tồn' thống nhất.
    """

    def __init__(self, symbol: str = "XAU_USDT"):
        self.symbol = symbol
        self.kline_queue = asyncio.Queue()
        self.macro_flag = "NEUTRAL"
        
        # 1. Khởi tạo Lõi Sinh Tồn
        self.survival_core = ExecutionRiskAgent()
        
        # 2. Khởi tạo Client WebSocket với khung nến 15 phút (Chuẩn F8 Alpha Pro)
        self.ws_client = MEXCWebSocketClient(symbol=self.symbol, interval="Min15", queue=self.kline_queue)
        
        # 3. Khởi tạo Quant Pricing Agent F8 Alpha Pro (Sniper 3 - 5 Lệnh A+/Ngày)
        self.quant_agent = QuantPricingAgent(
            queue=self.kline_queue,
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
            filter_asia_morning=True,
            default_volume=1.0
        )
        
        # 4. Khởi tạo Macro Sentiment Gatekeeper
        self.macro_agent = MacroSentimentAgent()

    async def vital_signs_monitor(self, interval_seconds: int = 5):
        """
        TASK 1: Kiểm tra nhịp tim sinh tồn định kỳ mỗi 5s.
        Nếu số dư cạn kiệt, hàm check_vital_signs() sẽ kích hoạt án tử Permadeath
        và gọi sys.exit(0) đánh sập toàn bộ hệ thống ngay lập tức.
        """
        logger.info(f"[ALIVE] Khởi động Module Giám sát Sinh tồn (Vital Monitor) - Chu kỳ {interval_seconds}s.")
        while True:
            try:
                # Kiểm tra số dư & sinh mệnh
                is_alive = self.survival_core.check_vital_signs()
                if not is_alive:
                    # Permadeath kill sequence đã được gọi bên trong check_vital_signs
                    break
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[VITAL MONITOR EXCEPTION] Lỗi kiểm tra sinh tồn: {e}")
                await asyncio.sleep(interval_seconds)

    async def macro_sentiment_loop(self, interval_seconds: int = 300):
        """
        TASK 2: Cập nhật đánh giá Vĩ mô bằng FinBERT (mỗi 5 phút).
        """
        logger.info(f"[GATEKEEPER] Khởi động Người Gác Cổng Vĩ Mô - Chu kỳ {interval_seconds}s.")
        while True:
            try:
                # Phân tích tin tức vĩ mô
                new_macro_state = self.macro_agent.get_macro_state()
                self.macro_flag = new_macro_state
                logger.info(f"[MACRO FLAG UPDATED] Cờ bảo vệ vĩ mô hiện tại: [{self.macro_flag}]")
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MACRO LOOP ERROR] Lỗi phân tích vĩ mô: {e}")
                await asyncio.sleep(30)

    async def handle_order_signal(self, signal_payload: dict):
        """
        TASK 3 CALLBACK: Xử lý tín hiệu giao dịch từ Quant Agent (BB + RSI Reversal).
        Chuyển tín hiệu kèm cờ Macro tới Lõi Sinh Tồn để kiểm duyệt & thực thi.
        """
        action = signal_payload.get("action", "")
        symbol = signal_payload.get("symbol", self.symbol)
        side = signal_payload.get("side", "LONG")
        volume = signal_payload.get("volume", 1.0)
        sl = signal_payload.get("sl")
        tp = signal_payload.get("tp")

        if "OPEN" in action:
            logger.info(
                f"[F8 ENTRY REQUEST] {action}: {side} {volume} {symbol} | "
                f"SL: {sl} | TP1: {signal_payload.get('tp1')} | TP2: {signal_payload.get('tp2')} | Cờ Vĩ Mô: [{self.macro_flag}]"
            )
            self.survival_core.place_order(
                symbol=symbol,
                side=side,
                volume=volume,
                macro_flag=self.macro_flag
            )
        elif "PARTIAL_TP1" in action:
            close_vol = volume * 0.50
            logger.info(
                f"[F8 TP1 REQUEST] Chốt 50% ({close_vol} {symbol}) tại {signal_payload.get('price')} "
                f"và Dời SL Khóa Lãi Dương tại {signal_payload.get('new_sl')}"
            )
            close_side = "CLOSE_LONG" if "LONG" in action else "CLOSE_SHORT"
            self.survival_core.place_order(
                symbol=symbol,
                side=close_side,
                volume=close_vol,
                macro_flag="NEUTRAL"
            )
        elif "TP2" in action or "PROFIT_LOCK_EXIT" in action or "BREAKEVEN_EXIT" in action or "CLOSE" in action:
            logger.info(f"[F8 EXIT REQUEST] {action} {symbol} tại giá {signal_payload.get('price')}")
            close_side = "CLOSE_LONG" if "LONG" in action else "CLOSE_SHORT"
            self.survival_core.place_order(
                symbol=symbol,
                side=close_side,
                volume=volume,
                macro_flag="NEUTRAL"
            )

    async def start(self):
        """
        Khởi động đồng thời tất cả các tác vụ bất đồng bộ.
        """
        logger.info("==========================================================")
        logger.info("       HỆ THỐNG AI AGENT GIAO DỊCH MEXC FUTURES")
        logger.info("              THỰC THỂ SINH TỒN (ALIVE)           ")
        logger.info("==========================================================")

        # Kiểm tra sinh tồn lần đầu tiên trước khi bật các động cơ
        self.survival_core.check_vital_signs()

        # Khởi chạy ban đầu đánh giá vĩ mô ngay lập tức
        self.macro_flag = self.macro_agent.get_macro_state()

        tasks = [
            asyncio.create_task(self.vital_signs_monitor(interval_seconds=5)),
            asyncio.create_task(self.ws_client.connect_and_listen()),
            asyncio.create_task(self.quant_agent.run_pricing_loop(self.handle_order_signal)),
            asyncio.create_task(self.macro_sentiment_loop(interval_seconds=300)),
        ]

        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, SystemExit):
            logger.info("[SYSTEM SHUTDOWN] Nhận tín hiệu dừng chương trình.")
        finally:
            self.ws_client.stop()
            for task in tasks:
                if not task.done():
                    task.cancel()


if __name__ == "__main__":
    try:
        trading_system = SurvivalTradingSystem(symbol="XAU_USDT")
        asyncio.run(trading_system.start())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
