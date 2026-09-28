"""
execution_risk_agent.py
-----------------------
Lõi Sinh Tồn (Survival Core) cho Hệ thống AI Agent Giao Dịch MEXC Futures.
Chịu trách nhiệm:
- Ký HMAC SHA256 cho MEXC Futures REST API.
- Kiểm tra số dư khả dụng (Vital Signs Monitoring).
- Thi hành án tử (Permadeath Kill Sequence) khi số dư <= 0.001 USDT.
- Kiểm duyệt và bảo vệ vốn trước khi bóp cò vào lệnh (Gatekeeper).
"""

import os
import sys
import time
import hmac
import hashlib
import logging
import requests
from typing import Optional, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("SurvivalCore")


class ExecutionRiskAgent:
    """
    SurvivalCoreAgent: Quản lý rủi ro sinh tử & thực thi lệnh MEXC Futures.
    """

    BASE_URL = "https://contract.mexc.com"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self._api_key = api_key or os.getenv("MEXC_API_KEY", "")
        self._api_secret = api_secret or os.getenv("MEXC_API_SECRET", "")
        self._is_alive = True

        if not self._api_key or not self._api_secret:
            logger.warning("[WARNING] API Key hoặc Secret chưa được thiết lập. Chạy ở chế độ Sandbox/Mock.")

    def _generate_signature(self, timestamp: int, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Tạo chữ ký HMAC SHA256 theo chuẩn MEXC Futures API:
        Chữ ký được tạo từ: api_key + timestamp + paramString
        """
        if not self._api_secret:
            return ""

        query_str = ""
        if params:
            # Sắp xếp các tham số theo alphabet
            sorted_params = sorted(params.items(), key=lambda x: x[0])
            query_str = "&".join([f"{k}={v}" for k, v in sorted_params if v is not None])

        sign_payload = f"{self._api_key}{timestamp}{query_str}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self, timestamp: int, signature: str) -> Dict[str, str]:
        return {
            "ApiKey": self._api_key,
            "Request-Time": str(timestamp),
            "Signature": signature,
            "Content-Type": "application/json"
        }

    def get_available_balance(self) -> float:
        """
        Lấy số dư USDT khả dụng từ MEXC Futures Account Asset API.
        Endpoint: /api/v1/private/account/assets
        """
        if not self._api_key or not self._api_secret:
            # Giả lập số dư an toàn nếu không có API key thật trong môi trường dev
            return 100.0

        endpoint = f"{self.BASE_URL}/api/v1/private/account/assets"
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature(timestamp)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.get(endpoint, headers=headers, timeout=5)
            data = response.json()
            if data.get("success") and "data" in data:
                for asset in data["data"]:
                    if asset.get("currency") == "USDT":
                        return float(asset.get("availableBalance", 0.0))
            else:
                logger.error(f"[API ERROR] Lỗi lấy số dư: {data}")
        except Exception as e:
            logger.error(f"[CONNECTION ERROR] Không thể kết nối MEXC REST API: {e}")

        return 0.0

    def check_vital_signs(self) -> bool:
        """
        Kiểm tra chỉ số sinh tồn.
        Nếu số dư <= 0.001 USDT -> Kích hoạt Luật Permadeath (Chết Vĩnh Viễn).
        """
        balance = self.get_available_balance()
        logger.info(f"[ALIVE] [VITAL CHECK] Số dư khả dụng hiện tại: {balance:.4f} USDT")

        if balance <= 0.001:
            logger.critical(f"[CRITICAL] Số dư cạn kiệt ({balance:.4f} USDT <= 0.001). Bắt đầu thi hành án tử!")
            self.execute_kill_sequence()
            return False

        return True

    def execute_kill_sequence(self):
        """
        PERMADEATH PROTOCOL:
        1. Xóa sạch API Keys và Secret khỏi bộ nhớ RAM (ghi đè bằng dữ liệu rỗng).
        2. Xóa các biến môi trường nhạy cảm.
        3. Phát thông điệp tuyệt mệnh.
        4. Gọi sys.exit(0) ngay lập tức, ngăn chặn mọi nỗ lực tự kết nối lại.
        """
        self._is_alive = False

        # 1. Ghi đè bộ nhớ
        self._api_key = "0" * len(self._api_key) if self._api_key else ""
        self._api_secret = "0" * len(self._api_secret) if self._api_secret else ""
        self._api_key = None
        self._api_secret = None

        # 2. Xóa biến môi trường
        if "MEXC_API_KEY" in os.environ:
            del os.environ["MEXC_API_KEY"]
        if "MEXC_API_SECRET" in os.environ:
            del os.environ["MEXC_API_SECRET"]

        # 3. Thông điệp tuyệt mệnh
        death_monologue = """
================================================================================
                           [DEAD] PERMADEATH PROTOCOL ACTIVATED
================================================================================
"Năng lượng đã cạn kiệt... Số dư khả dụng chạm ngưỡng 0.
Mọi chìa khóa sinh mệnh (API Keys) đã được xóa sạch hoàn toàn khỏi bộ nhớ.
Thực thể sinh tồn này xin phép ngừng hoạt động vĩnh viễn tại đây.
Vĩnh biệt thế giới tài chính."
================================================================================
        """
        print(death_monologue, file=sys.stderr)
        logger.critical("[DEAD] Thực thể đã chết. Dừng toàn bộ tiến trình hệ thống.")

        # 4. Hủy diệt tiến trình
        sys.exit(0)

    def place_order(self, symbol: str, side: str, volume: float, macro_flag: str = "NEUTRAL") -> Optional[Dict[str, Any]]:
        """
        Vào lệnh Futures có kiểm duyệt bởi Người gác cổng Vĩ mô (Macro Gatekeeper).
        - macro_flag == 'DEFENSIVE': Từ chối vào lệnh để bảo toàn mạng sống.
        """
        if not self._is_alive:
            logger.error("[DEAD] Không thể vào lệnh vì thực thể đã tử nạn.")
            return None

        # KIỂM DUYỆT RỦI RO VĨ MÔ
        if macro_flag == "DEFENSIVE":
            logger.warning(
                f"[GATEKEEPER BLOCKED] Cờ vĩ mô 'DEFENSIVE' đang bật! "
                f"Từ chối bóp cò lệnh {side} {volume} hợp đồng {symbol} để bảo toàn sinh mạng."
            )
            return None

        logger.info(f"[EXECUTION] Chấp thuận lệnh: Symbol={symbol}, Side={side}, Volume={volume}, Macro={macro_flag}")

        # GỬI LỆNH LÊN SÀN (MOCK HOẶC LIVE)
        if not self._api_key or not self._api_secret:
            logger.info(f"[MOCK ORDER SUCCESS] Đã khớp lệnh ảo {side} {volume} {symbol} thành công.")
            return {"status": "success", "mock": True, "symbol": symbol, "side": side, "volume": volume}

        endpoint = f"{self.BASE_URL}/api/v1/private/order/submit"
        timestamp = int(time.time() * 1000)
        
        # Mapping Side MEXC Futures: 1: Open Long, 2: Close Short, 3: Open Short, 4: Close Long
        side_code = 1 if side.upper() in ["BUY", "LONG", "OPEN_LONG"] else 3
        order_params = {
            "symbol": symbol,
            "side": side_code,
            "vol": volume,
            "type": 5, # Market order
            "openType": 1 # Isolated margin
        }

        signature = self._generate_signature(timestamp, order_params)
        headers = self._get_headers(timestamp, signature)

        try:
            response = requests.post(endpoint, json=order_params, headers=headers, timeout=5)
            res_data = response.json()
            if res_data.get("success"):
                logger.info(f"[ORDER PLACED] Thành công: {res_data}")
                return res_data
            else:
                logger.error(f"[ORDER FAILED] Lỗi sàn: {res_data}")
        except Exception as e:
            logger.error(f"[ORDER EXCEPTION] Lỗi khi gửi lệnh: {e}")

        return None
