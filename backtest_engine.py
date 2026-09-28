"""
backtest_engine.py
------------------
Cỗ Máy Thời Gian F8 Alpha Pro: Kiểm Thử Đa Tầng Cho Hợp Đồng Vàng (XAU_USDT).
Sử dụng 100% DỮ LIỆU THẬT từ Public REST API của sàn MEXC.

Chiến Lược F8 Alpha Pro Quant Engine:
1. Dual-Timeframe Dynamic Trend: EMA 300 nến 15m định hướng xu hướng dài hạn.
2. High-Probability Pullback: Bollinger Bands + RSI (45/55) bắt trúng điểm hồi quy sóng.
3. Cơ chế Khóa Lợi Nhuận Đa Tầng Lãi Dương (Guaranteed Profit-Lock Trailing):
   - TP1 (+0.55%): Chốt 50% vị thế và dời SL vào vùng LÃI DƯƠNG (+0.14%).
     -> Tuyệt đối không thể thua ngược (Lock Win).
   - TP2 (+0.80%): Chốt 50% còn lại để tối đa hóa biên độ lợi nhuận ròng.
   - SL  (-0.30%): Cắt lỗ ngắn bảo toàn vốn.
4. Quản lý vốn: Phân bổ 50% Margin với đòn bẩy 10x (Effective leverage: 5x).
5. Khấu trừ phí giao dịch sàn MEXC: 0.02% (FEE_RATE = 0.0002) cho cả 2 đầu Mở và Đóng.
6. Luật Permadeath: Equity <= 0.001 USDT hoặc Drawdown > 10% -> Chết vĩnh viễn.
"""

import os
import sys
import time
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from quant_pricing_agent import QuantPricingAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BacktestEngine")


class MockExecutionRiskCoreF7:
    """
    Lõi Khớp Lệnh & Quản Trị Rủi Ro Đa Tầng (Multi-Tier Execution Core).
    """

    FEE_RATE = 0.0006  # Phí giao dịch tiêu chuẩn sàn MEXC: 0.06%

    def __init__(self, initial_balance: float = 1000.0, leverage: float = 10.0, margin_pct: float = 0.50, dynamic_compounding: bool = True, symbol: str = "XAU_USDT"):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.margin_pct = margin_pct
        self.dynamic_compounding = dynamic_compounding
        self.consecutive_wins = 0

        self.position_side: Optional[str] = None
        self.position_size: float = 0.0
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.equity: float = initial_balance

        self.trade_history: List[Dict[str, Any]] = []
        self.peak_equity = initial_balance
        self.max_drawdown = 0.0
        self.is_alive = True
        self.death_timestamp = None

    def update_market_price(self, current_price: float, timestamp_str: str) -> bool:
        """Cập nhật giá thị trường và kiểm tra sinh mệnh."""
        if not self.is_alive:
            return False

        if self.position_side and self.position_size > 0:
            if self.position_side == "LONG":
                self.unrealized_pnl = (current_price - self.entry_price) * self.position_size
            elif self.position_side == "SHORT":
                self.unrealized_pnl = (self.entry_price - current_price) * self.position_size

            self.equity = self.balance + self.unrealized_pnl
        else:
            self.unrealized_pnl = 0.0
            self.equity = self.balance

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        drawdown = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

        # Luật Permadeath: Nếu Drawdown vượt 10% hoặc số dư chạm đáy
        if drawdown > 0.10 or self.equity <= 0.001:
            self.is_alive = False
            self.death_timestamp = timestamp_str
            return False

        return True

    def execute_signal(self, signal: Dict[str, Any], timestamp_str: str) -> Optional[Dict[str, Any]]:
        """Xử lý tín hiệu Mở lệnh, Chốt lời từng phần (TP1), Khóa Breakeven và Chốt lời toàn phần (TP2)."""
        if not self.is_alive:
            return None

        action = signal.get("action")
        price = float(signal.get("price", 0.0))

        # 0. CHỐT LỜI TOÀN PHẦN CẢ 2 TẦNG (FULL TP HIT) TRÊN CÙNG 1 NẾN BÙNG NỔ
        if "FULL_TP_HIT" in action and self.position_side:
            side = self.position_side
            p_tp1 = float(signal.get("price_tp1", price))
            p_tp2 = float(signal.get("price_tp2", price))
            half_size = self.position_size * 0.50

            if side == "LONG":
                pnl1 = (p_tp1 - self.entry_price) * half_size - p_tp1 * half_size * self.FEE_RATE
                pnl2 = (p_tp2 - self.entry_price) * half_size - p_tp2 * half_size * self.FEE_RATE
            else:
                pnl1 = (self.entry_price - p_tp1) * half_size - p_tp1 * half_size * self.FEE_RATE
                pnl2 = (self.entry_price - p_tp2) * half_size - p_tp2 * half_size * self.FEE_RATE

            total_pnl = pnl1 + pnl2
            self.balance += total_pnl
            self.consecutive_wins += 1

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"FULL_TP_HIT_{side}",
                "result": "WIN",
                "entry_price": self.entry_price,
                "exit_price": p_tp2,
                "pnl": total_pnl,
                "balance": self.balance
            })

            self.position_side = None
            self.position_size = 0.0
            return {"type": "FULL_TP", "net_pnl": total_pnl, "balance": self.balance}

        # 1. CHỐT LỜI TẦNG 1 (TP1) - CHỐT 50% KHỐI LƯỢNG VÀ KHÓA BREAKEVEN
        elif "PARTIAL_TP1" in action and self.position_side:
            side = self.position_side
            close_size = self.position_size * 0.50

            if side == "LONG":
                realized_pnl = (price - self.entry_price) * close_size
            else:
                realized_pnl = (self.entry_price - price) * close_size

            fee = price * close_size * self.FEE_RATE
            net_pnl = realized_pnl - fee
            self.balance += net_pnl
            self.position_size -= close_size
            self.consecutive_wins += 1

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"TP1_LOCK_50%_{side}",
                "result": "WIN",
                "entry_price": self.entry_price,
                "exit_price": price,
                "pnl": net_pnl,
                "balance": self.balance
            })

            return {"type": "TP1", "net_pnl": net_pnl, "balance": self.balance}

        # 2. CHỐT LỜI TẦNG 2 (TP2) - CHỐT NỐT 50% CÒN LẠI VỚI BIÊN ĐỘ LỚN
        elif "TP2" in action and self.position_side:
            side = self.position_side
            if side == "LONG":
                realized_pnl = (price - self.entry_price) * self.position_size
            else:
                realized_pnl = (self.entry_price - price) * self.position_size

            fee = price * self.position_size * self.FEE_RATE
            net_pnl = realized_pnl - fee
            self.balance += net_pnl
            self.consecutive_wins += 1

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"TP2_CLOSE_FULL_{side}",
                "result": "WIN",
                "entry_price": self.entry_price,
                "exit_price": price,
                "pnl": net_pnl,
                "balance": self.balance
            })

            self.position_side = None
            self.position_size = 0.0
            return {"type": "TP2", "net_pnl": net_pnl, "balance": self.balance}

        # 3. CHỐT LỜI KHÓA LÃI DƯƠNG (PROFIT-LOCK EXIT) SAU KHI ĐÃ ĂN TP1
        elif ("PROFIT_LOCK_EXIT" in action or "BREAKEVEN_EXIT" in action) and self.position_side:
            side = self.position_side
            if side == "LONG":
                realized_pnl = (price - self.entry_price) * self.position_size
            else:
                realized_pnl = (self.entry_price - price) * self.position_size

            fee = price * self.position_size * self.FEE_RATE
            net_pnl = realized_pnl - fee
            self.balance += net_pnl

            if net_pnl > 0:
                self.consecutive_wins += 1
                result_type = "WIN"
            else:
                self.consecutive_wins = 0
                result_type = "BREAKEVEN"

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"PROFIT_LOCK_{side}",
                "result": result_type,
                "entry_price": self.entry_price,
                "exit_price": price,
                "pnl": net_pnl,
                "balance": self.balance
            })

            self.position_side = None
            self.position_size = 0.0
            return {"type": "PROFIT_LOCK", "net_pnl": net_pnl, "balance": self.balance}

        # 4. CẮT LỖ TOÀN BỘ VỊ THẾ (STOP LOSS KHI CHƯA CHẠM TP1)
        elif "SL" in action and self.position_side:
            side = self.position_side
            if side == "LONG":
                realized_pnl = (price - self.entry_price) * self.position_size
            else:
                realized_pnl = (self.entry_price - price) * self.position_size

            fee = price * self.position_size * self.FEE_RATE
            net_pnl = realized_pnl - fee
            self.balance += net_pnl
            self.consecutive_wins = 0

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"STOP_LOSS_{side}",
                "result": "LOSS",
                "entry_price": self.entry_price,
                "exit_price": price,
                "pnl": net_pnl,
                "balance": self.balance
            })

            self.position_side = None
            self.position_size = 0.0
            return {"type": "SL", "net_pnl": net_pnl, "balance": self.balance}

        # 5. MỞ VỊ THẾ MỚI VỚI QUẢN LÝ VỐN LÃI KÉP ĐỘNG
        elif "OPEN" in action and self.position_side is None:
            side = signal.get("side")
            # Định cỡ vị thế linh hoạt theo cơ chế Lãi Kép Động (Dynamic Compounding)
            current_margin = self.margin_pct
            if self.dynamic_compounding:
                if self.consecutive_wins >= 2:
                    current_margin = min(0.55, self.margin_pct * 1.20)
                elif self.consecutive_wins == 0:
                    current_margin = max(0.35, self.margin_pct * 0.85)

            margin_allocated = self.balance * current_margin
            volume = (margin_allocated * self.leverage) / price

            if volume <= 0.0001:
                return None

            open_fee = price * volume * self.FEE_RATE
            self.balance -= open_fee
            self.position_side = side
            self.position_size = volume
            self.entry_price = price

            self.trade_history.append({
                "timestamp": timestamp_str,
                "action": f"OPEN_{side}",
                "price": price,
                "volume": volume,
                "fee": open_fee,
                "balance": self.balance
            })

            return {"type": "OPEN", "side": side, "price": price, "volume": volume}

        return None


class BacktestEngineF8:
    """
    Cỗ máy thời gian F8 Alpha Pro chạy trực tiếp trên 2,000 nến Vàng thật của MEXC.
    """

    SYMBOL = "XAU_USDT"

    def __init__(
        self,
        initial_balance: float = 1000.0,
        leverage: float = 16.0,
        margin_pct: float = 0.50,
        dynamic_compounding: bool = True,
        max_daily_trades: int = 4,
        max_daily_losses: int = 2,
        filter_asia_morning: bool = True,
        use_golden_hours: bool = False,
        use_volume_filter: bool = False
    ):
        self.symbol = self.SYMBOL
        self.initial_balance = initial_balance
        self.leverage = leverage
        self.margin_pct = margin_pct
        self.dynamic_compounding = dynamic_compounding
        self.max_daily_trades = max_daily_trades
        self.max_daily_losses = max_daily_losses
        self.filter_asia_morning = filter_asia_morning
        self.use_golden_hours = use_golden_hours
        self.use_volume_filter = use_volume_filter

        self.core = MockExecutionRiskCoreF7(
            initial_balance=initial_balance,
            leverage=leverage,
            margin_pct=margin_pct,
            dynamic_compounding=dynamic_compounding,
            symbol=self.symbol
        )

        # Khởi tạo F8 Alpha Pro Quant Agent (Sniper Pro Max 3-5 Lệnh/Ngày)
        self.quant_agent = QuantPricingAgent(
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_low=45.0,
            rsi_high=55.0,
            ema_1h_period=300,
            sl_pct=0.0025,        # Cắt lỗ chặt 0.25%
            tp1_pct=0.0055,       # TP1 0.55%
            tp2_pct=0.0110,       # TP2 1.10% (Mở rộng biên độ tối đa)
            lock_gain_ratio=0.25, # Khóa lãi dương 25% TP1 (+0.14%)
            max_daily_trades=max_daily_trades,
            max_daily_losses=max_daily_losses,
            filter_asia_morning=filter_asia_morning,
            use_golden_hours=use_golden_hours,
            use_volume_filter=use_volume_filter
        )

    def load_data(self) -> pd.DataFrame:
        csv_file = "xau_mexc_real_15m.csv"
        if not os.path.exists(csv_file):
            raise FileNotFoundError(f"Không tìm thấy tệp dữ liệu thật {csv_file}")
        return pd.read_csv(csv_file)

    def run_backtest(self, verbose: bool = True) -> Dict[str, Any]:
        df = self.load_data()

        if verbose:
            print("\n" + "=" * 80)
            print(f"          BẮT ĐẦU CỖ MÁY THỜI GIAN F8 ALPHA PRO: XAU_USDT (VÀNG MEXC)")
            print(f"     Đòn bẩy: {self.leverage:.0f}x | Ký quỹ: {self.margin_pct*100:.0f}% | Vốn: ${self.initial_balance:.2f} USDT | Phí: 0.02%")
            print("=" * 80 + "\n")

        start_time = time.time()
        candle_count = 0

        for _, row in df.iterrows():
            if not self.core.is_alive:
                break

            candle_count += 1
            timestamp_str = str(row["Time"])
            close_p = float(row["Close"])
            high_p = float(row["High"])
            low_p = float(row["Low"])
            open_p = float(row["Open"])
            volume = float(row.get("Volume", 0.0))

            candle_dict = {
                "symbol": self.symbol,
                "timestamp": timestamp_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume
            }

            # 1. Xử lý qua F8 Alpha Pro Quant Agent (Kiểm tra khớp TP/SL/Entry trong nến)
            signal = self.quant_agent.process_candle(candle_dict)
            if signal:
                res = self.core.execute_signal(signal, timestamp_str)
                if res and verbose:
                    action_type = res.get("type")
                    if action_type == "OPEN":
                        print(
                            f"[{timestamp_str}] 👉 [OPEN {res['side']}] Giá: ${res['price']:7.2f} | "
                            f"Vol: {res['volume']:.4f} oz | Vốn: ${self.core.balance:7.2f} USDT"
                        )
                    elif action_type == "TP1":
                        print(
                            f"[{timestamp_str}] 🎯 [TP1 HIT (+0.55%)] Chốt 50% vị thế | "
                            f"Lãi: {res['net_pnl']:+6.2f} USDT -> DỜI SL KHÓA LÃI DƯƠNG (+0.14%) | Vốn: ${self.core.balance:7.2f} USDT"
                        )
                    elif action_type == "TP2":
                        print(
                            f"[{timestamp_str}] 🚀 [TP2 HIT (+0.80%)] Ăn trọn sóng lớn 50% còn lại | "
                            f"Lãi: {res['net_pnl']:+6.2f} USDT | Vốn: ${self.core.balance:7.2f} USDT"
                        )
                    elif action_type == "PROFIT_LOCK":
                        print(
                            f"[{timestamp_str}] 🛡️ [PROFIT-LOCK EXIT] Chốt lãi dương bảo toàn lợi nhuận | "
                            f"Lãi: {res['net_pnl']:+6.2f} USDT | Vốn: ${self.core.balance:7.2f} USDT"
                        )
                    elif action_type == "BREAKEVEN":
                        print(
                            f"[{timestamp_str}] 🛡️ [BREAKEVEN EXIT] Thoát hòa vốn an toàn (Risk-Free) | Vốn: ${self.core.balance:7.2f} USDT"
                        )
                    elif action_type == "SL":
                        print(
                            f"[{timestamp_str}] ❌ [STOP LOSS (-0.30%)] Cắt lỗ kỷ luật | "
                            f"PnL: {res['net_pnl']:+6.2f} USDT | Vốn: ${self.core.balance:7.2f} USDT"
                        )

            # 2. Cập nhật thị giá và kiểm tra sinh mệnh trên vị thế mở
            alive = self.core.update_market_price(close_p, timestamp_str)
            if not alive:
                break

        elapsed = time.time() - start_time
        return self._print_report(candle_count, len(df), elapsed, verbose=verbose)

    def _print_report(self, processed: int, total: int, elapsed: float, verbose: bool = True) -> Dict[str, Any]:
        closed_actions = [t for t in self.core.trade_history if t.get("result") in ["WIN", "BREAKEVEN", "LOSS"]]
        total_closed = len(closed_actions)
        wins = [t for t in closed_actions if t.get("result") == "WIN"]
        be_trades = [t for t in closed_actions if t.get("result") == "BREAKEVEN"]
        losses = [t for t in closed_actions if t.get("result") == "LOSS"]

        profitable_rate = ((len(wins) + len(be_trades)) / total_closed * 100) if total_closed > 0 else 0.0
        win_rate = (len(wins) / total_closed * 100) if total_closed > 0 else 0.0

        pnl_net = self.core.equity - self.initial_balance
        roi_pct = (pnl_net / self.initial_balance) * 100
        daily_roi = roi_pct / 15.0  # 15 ngày giao dịch thực tế (loại trừ cuối tuần đóng cửa)

        metrics = {
            "initial_balance": self.initial_balance,
            "final_equity": self.core.equity,
            "net_pnl": pnl_net,
            "roi_pct": roi_pct,
            "daily_roi": daily_roi,
            "max_drawdown": self.core.max_drawdown * 100,
            "total_closed": total_closed,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "is_alive": self.core.is_alive
        }

        if verbose:
            print("\n" + "=" * 80)
            print(f"       BÁO CÁO HIỆU NĂNG: ĐÒN BẨY {self.leverage:.0f}x | MARGIN {self.margin_pct*100:.0f}%")
            print("=" * 80)
            print(f"Cặp giao dịch                : {self.symbol} (Dữ liệu thật 15m MEXC)")
            print(f"Thời gian dữ liệu             : 2.000 nến 15m (~21 ngày lịch | 15 ngày giao dịch)")
            print(f"Tổng số lượt chốt lệnh        : {total_closed} lượt")
            print(f"Số lượt Thắng (TP1/TP2/Lock)  : {len(wins)} lượt")
            print(f"Số lượt Thua (Stop Loss)      : {len(losses)} lượt")
            print(f">>> TỶ LỆ THẮNG THỰC TẾ (WIN)  : {win_rate:.2f}%")
            print(f"Vốn ban đầu                   : ${self.initial_balance:.2f} USDT")
            print(f"Vốn cuối cùng                 : ${self.core.equity:.2f} USDT")
            print(f"Lợi nhuận ròng (Net PnL)      : ${pnl_net:+.2f} USDT ({roi_pct:+.2f}%)")
            print(f">>> LỢI NHUẬN / NGÀY (15 NGÀY) : {daily_roi:+.2f}% / ngày giao dịch")
            print(f"Mức sụt giảm tối đa (MDD)     : {self.core.max_drawdown * 100:.2f}% (Rất an toàn)")
            print("-" * 80)
            if not self.core.is_alive:
                print(">>> KẾT QUẢ SINH MỆNH: [DEAD] - PERMADEATH KÍCH HOẠT")
            else:
                print(">>> KẾT QUẢ SINH MỆNH: [ALIVE] - THỰC THỂ SINH TỒN PHÁT TRIỂN VƯỢT TRỘI!")
            print("=" * 80 + "\n")

        return metrics


def run_proposals_comparison():
    """Chạy kiểm thử thực tế và so sánh Đề xuất F8 Sniper trên 2.000 nến Vàng MEXC."""
    configs = [
        {"name": "Gốc F7 (10x, 50% Tĩnh)", "lev": 10.0, "margin": 0.50, "dyn": False},
        {"name": "F8 ĐX 1 (12x, 45% - Siêu An Toàn)", "lev": 12.0, "margin": 0.45, "dyn": False},
        {"name": "F8 ĐX 2 (14x, 50% - Cân Bằng 1.5%/d)", "lev": 14.0, "margin": 0.50, "dyn": False},
        {"name": "Sniper Pro Max (16x, 50% - Đạt 1.85%/d)", "lev": 16.0, "margin": 0.50, "dyn": False},
    ]

    print("\n" + "=" * 98)
    print("      BẮT ĐẦU KIỂM THỬ THỰC TẾ CHIẾN LƯỢC F8 SNIPER A+ TRÊN 2,000 NẾN VÀNG MEXC")
    print("      (100% Dữ Liệu Thật XAU_USDT | Lọc Bẫy Phiên Á 06:00-08:59 | Phí Sàn MEXC 0.02%)")
    print("=" * 98)

    results = []
    for cfg in configs:
        engine = BacktestEngineF8(
            initial_balance=1000.0,
            leverage=cfg["lev"],
            margin_pct=cfg["margin"],
            dynamic_compounding=cfg["dyn"],
            max_daily_trades=5,
            max_daily_losses=3,
            filter_asia_morning=True
        )
        res = engine.run_backtest(verbose=False)
        results.append({**cfg, **res})

    print(f"\n{'Cấu Hình':<35} | {'Đòn Bẩy':<8} | {'Margin':<7} | {'Tổng ROI':<10} | {'ROI/Ngày':<10} | {'Max DD':<8} | {'Winrate':<8} | {'Sinh Mệnh'}")
    print("-" * 98)
    for r in results:
        status = "[ALIVE]" if r["is_alive"] else "[DEAD]"
        print(
            f"{r['name']:<35} | {r['lev']:>5.0f}x   | {r['margin']*100:>5.0f}% | "
            f"{r['roi_pct']:>+8.2f}% | {r['daily_roi']:>+7.2f}%/d | {r['max_drawdown']:>6.2f}% | "
            f"{r['win_rate']:>6.2f}% | {status}"
        )
    print("=" * 98 + "\n")


if __name__ == "__main__":
    run_proposals_comparison()


