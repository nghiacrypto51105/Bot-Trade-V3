"""
mexc_trading_env.py
-------------------
Môi Trường Huấn Luyện Học Tăng Cường Tối Ưu (F6 Gymnasium Trading Environment).
Kế thừa gymnasium.Env tạo lớp MEXCSurvivalEnv cho Thực Thể Sinh Tồn XAU_USDT.

Nâng cấp F6:
1. Phí giao dịch thực tế sàn MEXC: 0.02% (FEE_RATE = 0.0002) cho mỗi lượt Mở và Đóng.
2. BỘ LỌC XU HƯỚNG ADX (14) CỨNG:
   - Chỉ cho phép vào lệnh (LONG/SHORT) khi ADX > 25 (xác nhận thị trường có xu hướng mạnh).
   - BẮT BUỘC ĐỨNG NGOÀI (HOLD) khi ADX <= 25 (thị trường đi ngang sideway/chop). Nếu AI cố tình vào lệnh sẽ bị ép về HOLD và phạt nhẹ.
3. TỐI ƯU HÀM THƯỞNG CHO MỤC TIÊU SINH LỜI CAO:
   - Chốt lời (Take Profit): Thưởng cực mạnh (+200.0 điểm + thưởng theo % Net PnL).
   - Cắt lỗ (Stop Loss): Phạt nặng (-30.0 điểm) để AI chỉ chọn những điểm vào lệnh cực kỳ chất lượng.
   - Hành động HOLD: Giảm nhẹ hình phạt, đặc biệt thưởng thêm khi kiên nhẫn đứng ngoài lúc ADX <= 25.
   - ÁN TỬ (Permadeath): Nếu Drawdown > 10%, phạt ngay -1000.0 điểm và terminated = True.
"""

import sys
import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from advanced_features import FEATURE_COLUMNS, extract_features


class MEXCSurvivalEnv(gym.Env):
    """
    Phòng tập huấn luyện Gymnasium F6 cho AI Agent giao dịch Vàng MEXC Futures.
    """

    metadata = {"render_modes": ["human"]}

    # Phí giao dịch tiêu chuẩn sàn MEXC: 0.06%
    FEE_RATE = 0.0006

    def __init__(
        self,
        df: pd.DataFrame,
        initial_balance: float = 1000.0,
        leverage: float = 10.0,
        max_drawdown_limit: float = 0.10,  # Ngưỡng sụt giảm vốn tối đa 10% (Án tử)
        position_size_pct: float = 0.20   # Phân bổ 20% vốn cho mỗi lệnh
    ):
        super().__init__()

        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.leverage = leverage
        self.max_drawdown_limit = max_drawdown_limit
        self.position_size_pct = position_size_pct

        # 1. Action Space: Discrete(3) -> 0: Hold/Flat, 1: Long, 2: Short
        self.action_space = spaces.Discrete(3)

        # 2. Observation Space: 9 features thị trường (có NORM_ADX) + 3 features tài khoản = 12
        self.feature_names = FEATURE_COLUMNS
        total_obs_dim = len(self.feature_names) + 3
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(total_obs_dim,),
            dtype=np.float32
        )

        # Trạng thái môi trường
        self.current_step = 0
        self.total_steps = len(self.df) - 1

        self.balance = initial_balance
        self.equity = initial_balance
        self.peak_equity = initial_balance
        self.max_drawdown = 0.0

        # Trạng thái vị thế
        self.position_side: Optional[str] = None
        self.position_size: float = 0.0
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.take_profit: float = 0.0

        # Thống kê
        self.trades = []
        self.win_count = 0
        self.loss_count = 0
        self.consecutive_idle_steps = 0

    def _get_observation(self) -> np.ndarray:
        """Trích xuất vector quan sát hiện tại kết hợp thị trường và tài khoản."""
        row = self.df.iloc[self.current_step]

        # 9 Đặc trưng thị trường (bao gồm NORM_ADX)
        market_feats = [float(row[col]) for col in self.feature_names]

        # 3 Đặc trưng tài khoản
        pos_code = 0.0
        if self.position_side == "LONG":
            pos_code = 1.0
        elif self.position_side == "SHORT":
            pos_code = -1.0

        unrealized_pnl_pct = 0.0
        if self.position_side and self.entry_price > 0:
            current_close = float(row["Close"])
            if self.position_side == "LONG":
                unrealized_pnl_pct = (current_close - self.entry_price) / self.entry_price
            else:
                unrealized_pnl_pct = (self.entry_price - current_close) / self.entry_price

        drawdown_pct = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0

        account_feats = [pos_code, unrealized_pnl_pct * 10.0, drawdown_pct * 10.0]
        obs = np.array(market_feats + account_feats, dtype=np.float32)
        obs = np.nan_to_num(obs, nan=0.0, posinf=1.0, neginf=-1.0)
        return obs

    def reset(self, seed: Optional[int] = None, options: Optional[dict] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Bắt đầu lại chu kỳ huấn luyện mới."""
        super().reset(seed=seed)

        self.current_step = 0
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.peak_equity = self.initial_balance
        self.max_drawdown = 0.0

        self.position_side = None
        self.position_size = 0.0
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

        self.trades = []
        self.win_count = 0
        self.loss_count = 0
        self.consecutive_idle_steps = 0

        obs = self._get_observation()
        info = {"balance": self.balance, "equity": self.equity, "drawdown": 0.0}
        return obs, info

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """
        Duyệt qua 1 nến 15m với đầy đủ phí MEXC 0.02%, bộ lọc cứng ADX > 25 và hàm thưởng tối ưu.
        """
        self.current_step += 1
        terminated = False
        truncated = False
        reward = 0.0

        if self.current_step >= self.total_steps:
            truncated = True

        row = self.df.iloc[self.current_step]
        close_p = float(row["Close"])
        high_p = float(row["High"])
        low_p = float(row["Low"])
        atr_val = float(row["ATR_14"])
        adx_val = float(row.get("ADX_14", 30.0))

        # 1. KIỂM TRA ĐIỀU KIỆN CHỐT LỜI / CẮT LỖ CHO VỊ THẾ HIỆN TẠI
        if self.position_side == "LONG":
            if high_p >= self.take_profit:
                # Chốt lời thành công
                realized_pnl = (self.take_profit - self.entry_price) * self.position_size
                fee = self.take_profit * self.position_size * self.FEE_RATE
                net_pnl = realized_pnl - fee
                pnl_pct = (net_pnl / self.balance) if self.balance > 0 else 0.0
                self.balance += net_pnl
                self.win_count += 1
                self.trades.append({"action": "CLOSE_LONG_TP", "pnl": net_pnl, "win": True})

                # TĂNG MẠNH THƯỞNG TP (+200.0) VÀ THƯỞNG THEO % LÃI THỰC TẾ
                reward += 200.0 + (pnl_pct * 1500.0)
                self.position_side = None
                self.position_size = 0.0
                self.consecutive_idle_steps = 0

            elif low_p <= self.stop_loss:
                # Cắt lỗ
                realized_pnl = (self.stop_loss - self.entry_price) * self.position_size
                fee = self.stop_loss * self.position_size * self.FEE_RATE
                net_pnl = realized_pnl - fee
                pnl_pct = (net_pnl / self.balance) if self.balance > 0 else 0.0
                self.balance += net_pnl
                self.loss_count += 1
                self.trades.append({"action": "CLOSE_LONG_SL", "pnl": net_pnl, "win": False})

                # PHẠT NẶNG HƠN KHI DÍNH SL (-30.0) ĐỂ ÉP CHỌN ĐIỂM VÀO CỰC KỲ CHẤT LƯỢNG
                reward -= 30.0 + abs(pnl_pct * 400.0)
                self.position_side = None
                self.position_size = 0.0
                self.consecutive_idle_steps = 0

        elif self.position_side == "SHORT":
            if low_p <= self.take_profit:
                # Chốt lời thành công
                realized_pnl = (self.entry_price - self.take_profit) * self.position_size
                fee = self.take_profit * self.position_size * self.FEE_RATE
                net_pnl = realized_pnl - fee
                pnl_pct = (net_pnl / self.balance) if self.balance > 0 else 0.0
                self.balance += net_pnl
                self.win_count += 1
                self.trades.append({"action": "CLOSE_SHORT_TP", "pnl": net_pnl, "win": True})

                # TĂNG MẠNH THƯỞNG TP (+200.0) VÀ THƯỞNG THEO % LÃI THỰC TẾ
                reward += 200.0 + (pnl_pct * 1500.0)
                self.position_side = None
                self.position_size = 0.0
                self.consecutive_idle_steps = 0

            elif high_p >= self.stop_loss:
                # Cắt lỗ
                realized_pnl = (self.entry_price - self.stop_loss) * self.position_size
                fee = self.stop_loss * self.position_size * self.FEE_RATE
                net_pnl = realized_pnl - fee
                pnl_pct = (net_pnl / self.balance) if self.balance > 0 else 0.0
                self.balance += net_pnl
                self.loss_count += 1
                self.trades.append({"action": "CLOSE_SHORT_SL", "pnl": net_pnl, "win": False})

                # PHẠT NẶNG HƠN KHI DÍNH SL (-30.0)
                reward -= 30.0 + abs(pnl_pct * 400.0)
                self.position_side = None
                self.position_size = 0.0
                self.consecutive_idle_steps = 0

        # 2. BỘ LỌC XU HƯỚNG ADX CỨNG & XỬ LÝ HÀNH ĐỘNG MỚI
        if self.position_side is None:
            # ĐIỀU KIỆN LỌC CỨNG: Nếu ADX <= 25 (Thị trường đi ngang), BẮT BUỘC ĐỨNG NGOÀI (HOLD)
            if adx_val <= 25.0:
                if action in [1, 2]:
                    # Nếu AI cố tình vào lệnh trong vùng sideway -> Ép về HOLD và phạt nhắc nhở
                    action = 0
                    reward -= 2.0
                else:
                    # Thưởng nhẹ khi AI kiên nhẫn đứng ngoài lúc thị trường sideway
                    reward += 0.1
                self.consecutive_idle_steps = 0

            else:
                # KHI ADX > 25: XÁC NHẬN CÓ XU HƯỚNG MẠNH -> CHO PHÉP MỞ VỊ THẾ
                if action == 1:  # Vào LONG
                    margin = self.balance * self.position_size_pct
                    vol = (margin * self.leverage) / close_p
                    open_fee = close_p * vol * self.FEE_RATE
                    self.balance -= open_fee

                    self.position_side = "LONG"
                    self.position_size = vol
                    self.entry_price = close_p
                    # SL: 1.5 * ATR, TP: 3.0 * ATR
                    self.stop_loss = close_p - (1.5 * atr_val)
                    self.take_profit = close_p + (3.0 * atr_val)
                    self.consecutive_idle_steps = 0

                elif action == 2:  # Vào SHORT
                    margin = self.balance * self.position_size_pct
                    vol = (margin * self.leverage) / close_p
                    open_fee = close_p * vol * self.FEE_RATE
                    self.balance -= open_fee

                    self.position_side = "SHORT"
                    self.position_size = vol
                    self.entry_price = close_p
                    # SL: 1.5 * ATR, TP: 3.0 * ATR
                    self.stop_loss = close_p + (1.5 * atr_val)
                    self.take_profit = close_p - (3.0 * atr_val)
                    self.consecutive_idle_steps = 0

                elif action == 0:  # Đứng ngoài lúc có xu hướng
                    self.consecutive_idle_steps += 1
                    # Chỉ phạt rất nhẹ nếu bỏ lỡ xu hướng quá lâu (> 15 nến)
                    if self.consecutive_idle_steps > 15:
                        reward -= 0.02 * min(self.consecutive_idle_steps - 15, 5)

        # 3. TÍNH TOÁN EQUITY VÀ DRAWDOWN
        if self.position_side == "LONG":
            unrealized = (close_p - self.entry_price) * self.position_size
            self.equity = self.balance + unrealized
        elif self.position_side == "SHORT":
            unrealized = (self.entry_price - close_p) * self.position_size
            self.equity = self.balance + unrealized
        else:
            self.equity = self.balance

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        current_dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd

        # 4. LUẬT SINH TỒN & ÁN TỬ (DEATH PENALTY)
        # Nếu Drawdown > 10% -> Game Over!
        if current_dd > self.max_drawdown_limit or self.equity <= 0.001:
            reward -= 1000.0  # Phạt cực nặng khi vi phạm sinh tồn
            terminated = True

        # Thưởng tích lũy khi gia tăng vốn an toàn
        if self.equity > self.initial_balance:
            reward += (self.equity - self.initial_balance) / self.initial_balance * 0.3

        obs = self._get_observation()
        info = {
            "balance": self.balance,
            "equity": self.equity,
            "drawdown": current_dd,
            "wins": self.win_count,
            "losses": self.loss_count,
            "trades": len(self.trades)
        }

        return obs, reward, terminated, truncated, info
