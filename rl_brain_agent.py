"""
rl_brain_agent.py
-----------------
Bộ Não Học Tăng Cường (Reinforcement Learning Brain - F5).
Sử dụng thuật toán Proximal Policy Optimization (PPO) từ Stable-Baselines3.

Chức năng:
1. Huấn luyện Agent với PPO trên môi trường MEXCSurvivalEnv (MlpPolicy).
2. Phân chia tập dữ liệu: 80% Train, 20% Test (Out-of-sample).
3. Đặt mục tiêu huấn luyện: Tối ưu hóa điểm thưởng sinh tồn, tránh né Án Tử (Drawdown > 10%).
4. Lưu model đã huấn luyện thành file: survival_gold_ppo.zip.
5. Kiểm thử (Backtest) trên tập dữ liệu Test chưa từng thấy:
   - In ra Winrate, Total PnL, Max Drawdown và Tình trạng sinh tồn.
"""

import os
import sys
import time
import argparse
import logging
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

from advanced_features import extract_features
from mexc_trading_env import MEXCSurvivalEnv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("RLBrainAgent")


class RLBrainAgent:
    """
    Agent quản lý mô hình RL PPO cho Thực Thể Sinh Tồn XAU_USDT.
    """

    MODEL_FILE = "survival_gold_ppo.zip"

    def __init__(self, data_path: str = "xau_mexc_real_15m.csv", initial_balance: float = 1000.0, leverage: float = 10.0):
        self.data_path = data_path
        self.initial_balance = initial_balance
        self.leverage = leverage

        self.df_train = None
        self.df_test = None
        self._prepare_data()

    def _prepare_data(self, train_split: float = 0.80):
        """Đọc và trích xuất đặc trưng MTF, chia tập Train/Test độc lập."""
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Không tìm thấy file dữ liệu: {self.data_path}")

        logger.info(f"[DATA LOADING] Đang đọc dữ liệu từ: {self.data_path}...")
        df_raw = pd.read_csv(self.data_path)
        
        logger.info("[FEATURE EXTRACTION] Đang tính toán các chỉ báo MTF (RSI, BB, EMA, VWAP, ATR)...")
        df_features = extract_features(df_raw)
        
        total_rows = len(df_features)
        split_idx = int(total_rows * train_split)

        self.df_train = df_features.iloc[:split_idx].reset_index(drop=True)
        self.df_test = df_features.iloc[split_idx:].reset_index(drop=True)

        logger.info(
            f"[DATA SPLIT] Tổng nến sạch: {total_rows:,} | "
            f"Tập Train: {len(self.df_train):,} nến | Tập Test (Out-of-sample): {len(self.df_test):,} nến"
        )

    def train_agent(self, total_timesteps: int = 100000, model_save_path: str = MODEL_FILE):
        """
        Huấn luyện PPO trên môi trường MEXCSurvivalEnv.
        """
        print("\n" + "=" * 80)
        print("          BẮT ĐẦU QUÁ TRÌNH HUẤN LUYỆN BỘ NÃO RL (PPO) - XAU_USDT")
        print(f"  Tập Train: {len(self.df_train):,} nến | Timesteps: {total_timesteps:,} | Vốn: ${self.initial_balance:.2f} USDT")
        print("=" * 80 + "\n")

        # Tạo môi trường vector hóa
        def make_env():
            return MEXCSurvivalEnv(
                df=self.df_train,
                initial_balance=self.initial_balance,
                leverage=self.leverage,
                max_drawdown_limit=0.10  # Giới hạn Drawdown 10%
            )

        vec_env = DummyVecEnv([make_env])

        # Khởi tạo mô hình PPO với MlpPolicy
        model = PPO(
            policy="MlpPolicy",
            env=vec_env,
            learning_rate=3e-4,
            n_steps=2048,
            batch_size=64,
            n_epochs=10,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,  # Khuyến khích khám phá chiến lược
            verbose=1
        )

        start_time = time.time()
        logger.info(f"[TRAINING START] Bắt đầu học tăng cường ({total_timesteps:,} bước)...")
        model.learn(total_timesteps=total_timesteps)
        training_time = time.time() - start_time

        # Lưu model
        model.save(model_save_path)
        logger.info(f"[MODEL SAVED] Đã lưu mô hình huấn luyện thành công tại: {model_save_path}")
        print(f"\n>>> Hoàn thành huấn luyện trong {training_time:.2f} giây! Model: {model_save_path}\n")

    def backtest_trained_model(self, model_path: str = MODEL_FILE):
        """
        Kiểm thử (Backtest) mô hình đã lưu trên tập dữ liệu Test chưa từng thấy.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Không tìm thấy model tại: {model_path}. Vui lòng chạy huấn luyện trước.")

        print("\n" + "=" * 80)
        print("       BẮT ĐẦU BACKTEST BỘ NÃO RL ĐÃ HUẤN LUYỆN TRÊN DỮ LIỆU TEST (OUT-OF-SAMPLE)")
        print(f"  Tập Test: {len(self.df_test):,} nến 15m | Model: {model_path} | Vốn: ${self.initial_balance:.2f} USDT")
        print("=" * 80 + "\n")

        test_env = MEXCSurvivalEnv(
            df=self.df_test,
            initial_balance=self.initial_balance,
            leverage=self.leverage,
            max_drawdown_limit=0.10
        )

        model = PPO.load(model_path)
        obs, info = test_env.reset()

        step_count = 0
        action_names = {0: "HOLD", 1: "LONG", 2: "SHORT"}

        for i in range(len(self.df_test) - 1):
            action, _ = model.predict(obs, deterministic=True)
            action_int = int(action)

            obs, reward, terminated, truncated, info = test_env.step(action_int)
            step_count += 1

            if step_count % 50 == 0:
                print(
                    f"[Nến #{step_count:3d}] Hành động AI: {action_names[action_int]:<5} | "
                    f"Vốn: ${info['equity']:7.2f} USDT | Drawdown: {info['drawdown']:.2%} | "
                    f"Lệnh: {info['trades']} (Thắng: {info['wins']} - Thua: {info['losses']})"
                )

            if terminated:
                print("\n>>> CẢNH BÁO ÁN TỬ: Sụt giảm vốn vượt ngưỡng 10%! Kích hoạt Permadeath Game Over.")
                break

            if truncated:
                break

        # BÁO CÁO TỔNG KẾT TEST
        print("\n" + "=" * 80)
        print("            BÁO CÁO HIỆU NĂNG BỘ NÃO RL TRÊN TẬP DỮ LIỆU TEST")
        print("=" * 80)

        total_trades = info["trades"]
        wins = info["wins"]
        losses = info["losses"]
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        pnl_net = info["equity"] - self.initial_balance
        roi_pct = (pnl_net / self.initial_balance) * 100

        print(f"Tập kiểm thử                 : {len(self.df_test):,} nến 15m Vàng (Chưa từng thấy lúc train)")
        print(f"Số bước đã chạy              : {step_count} nến")
        print(f"Tổng số lệnh đã khớp         : {total_trades} lệnh")
        print(f"Số lệnh Thắng (Take Profit)  : {wins} lệnh")
        print(f"Số lệnh Thua  (Stop Loss)    : {losses} lệnh")
        print(f">>> TỶ LỆ THẮNG (WINRATE)     : {win_rate:.2f}%")
        print(f"Vốn ban đầu                  : ${self.initial_balance:.2f} USDT")
        print(f"Vốn cuối cùng                : ${info['equity']:.2f} USDT")
        print(f"Lợi nhuận ròng (Net PnL)     : ${pnl_net:+.2f} USDT ({roi_pct:+.2f}%)")
        print(f"Mức sụt giảm tối đa (MDD)    : {test_env.max_drawdown * 100:.2f}% (Giới hạn cho phép: 10.0%)")

        print("-" * 80)
        if terminated:
            print(">>> KẾT QUẢ: [DEAD] - BỊ THANH LÝ DO DRAWDOWN > 10% (ÁN TỬ)")
        else:
            print(">>> KẾT QUẢ: [ALIVE] - THỰC THỂ SINH TỒN THÀNH CÔNG VƯỢT QUA TẬP TEST NGOÀI MẪU!")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RL Brain Agent (PPO) cho MEXC Survival Entity")
    parser.add_argument("--train", action="store_true", help="Chạy chế độ huấn luyện PPO")
    parser.add_argument("--backtest", action="store_true", help="Chạy kiểm thử mô hình đã huấn luyện")
    parser.add_argument("--timesteps", type=int, default=300000, help="Số bước huấn luyện (mặc định: 300000)")
    parser.add_argument("--data", type=str, default="xau_mexc_real_15m.csv", help="Đường dẫn file dữ liệu CSV")
    args = parser.parse_args()

    agent = RLBrainAgent(data_path=args.data)

    if args.train:
        agent.train_agent(total_timesteps=args.timesteps)
    if args.backtest or (not args.train and not args.backtest):
        # Nếu chưa chỉ định gì thì mặc định chạy train ngắn & backtest
        if not os.path.exists("survival_gold_ppo.zip"):
            print("Chưa có model sẵn. Đang khởi chạy huấn luyện trước...")
            agent.train_agent(total_timesteps=args.timesteps)
        agent.backtest_trained_model()
