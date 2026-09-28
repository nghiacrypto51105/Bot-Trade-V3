"""
test_survival_system.py
-----------------------
Kịch bản tự động kiểm thử toàn diện các tính năng của Hệ thống Sinh Tồn:
1. [ALIVE] Kiểm tra trạng thái bình thường (Số dư > 0).
2. [MACRO GATEKEEPER] Kiểm tra cờ tâm lý và chặn lệnh khi DEFENSIVE.
3. [QUANT & ORDER] Khớp lệnh thử nghiệm khi cờ bình thường.
4. [PERMADEATH & DEAD] Kích hoạt giả lập cạn kiệt tài khoản -> Xóa Key & Tự hủy sys.exit(0).
"""

import sys
import time
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from execution_risk_agent import ExecutionRiskAgent
from macro_sentiment_agent import MacroSentimentAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SystemTest")

def run_comprehensive_test():
    print("=" * 70)
    print("      BẮT ĐẦU CHẠY KIỂM THỬ HỆ THỐNG 'THỰC THỂ SINH TỒN'")
    print("=" * 70)

    # 1. Khởi tạo Agent
    agent = ExecutionRiskAgent()
    # Mock số dư ban đầu khỏe mạnh 100 USDT để test các bước vận hành
    agent.get_available_balance = lambda: 100.0
    macro_agent = MacroSentimentAgent()

    # 2. Test Vital Signs khi còn sống
    print("\n>>> BƯỚC 1: Kiểm tra nhịp tim sinh tồn bình thường")
    agent.check_vital_signs()

    # 3. Test Macro Sentiment & Gatekeeper Block
    print("\n>>> BƯỚC 2: Kiểm thử Người Gác Cổng Vĩ Mô (Tin Xấu -> DEFENSIVE)")
    bad_news = [
        "Major exchange suffers catastrophic flash crash and insolvency",
        "SEC files emergency injunction against crypto asset liquidity",
        "Massive panic dump triggers historic market liquidations"
    ]
    macro_flag = macro_agent.get_macro_state(sample_headlines=bad_news)
    print(f"Cờ Vĩ Mô thu được: [{macro_flag}]")

    print("\n>>> BƯỚC 3: Thử bóp cò khi cờ là DEFENSIVE (Kỳ vọng: BỊ CHẶN)")
    order_result = agent.place_order(symbol="BTC_USDT", side="LONG", volume=0.5, macro_flag=macro_flag)
    assert order_result is None, "Lỗi: Lệnh không bị chặn khi cờ DEFENSIVE!"

    print("\n>>> BƯỚC 4: Thử bóp cò khi cờ là AGGRESSIVE (Kỳ vọng: CHẤP THUẬN)")
    good_news = [
        "Global regulatory approval granted for instant ETF settlements",
        "Record institutional capital inflow surges crypto market to all-time highs"
    ]
    good_macro_flag = macro_agent.get_macro_state(sample_headlines=good_news)
    order_result_2 = agent.place_order(symbol="BTC_USDT", side="LONG", volume=0.5, macro_flag=good_macro_flag)
    assert order_result_2 is not None, "Lỗi: Lệnh không được duyệt khi cờ AGGRESSIVE!"

    # 4. Test Permadeath
    print("\n>>> BƯỚC 5: Kiểm thử LUẬT PERMADEATH (Số dư = 0 -> Tự hủy)")
    print("Ghi đè số dư = 0.0000 USDT để kích hoạt Án tử...")
    
    # Mock số dư = 0 để kích hoạt kill sequence
    agent.get_available_balance = lambda: 0.0000
    
    print("Chuẩn bị gọi check_vital_signs()... Toàn bộ hệ thống sẽ sập và thoát.")
    time.sleep(1)
    agent.check_vital_signs()


if __name__ == "__main__":
    run_comprehensive_test()
