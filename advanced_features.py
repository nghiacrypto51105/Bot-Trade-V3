"""
advanced_features.py
--------------------
Bộ Xử Lý Đặc Trưng Đa Khung Thời Gian & Bộ Lọc Xu Hướng ADX (F6).
Chuyên dụng cho Vàng XAU_USDT kết hợp Học Tăng Cường (Reinforcement Learning).

Chức năng:
1. Tính toán các chỉ báo kỹ thuật cốt lõi:
   - RSI (14)
   - Bollinger Bands (20, 2.0) & %B, BB_width
   - EMA 50 và EMA 200
   - VWAP (Volume Weighted Average Price)
   - ATR (14) (Average True Range)
   - ADX (14) (Average Directional Index) - BỘ LỌC XU HƯỚNG CỨNG (Trending > 25)
2. Mô phỏng Đa khung thời gian (MTF) trực tiếp trên nến 15m:
   - Khung 1H: EMA 200 (1H) = EMA 800 (15m)
   - Khung 4H: EMA 200 (4H) = EMA 3200 (15m) / hoặc tỷ lệ thích ứng
3. Làm sạch dữ liệu (dropna) và chuẩn hóa đặc trưng cho Gym Observation Space.
"""

import sys
import numpy as np
import pandas as pd
from typing import List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Tính chỉ số RSI chu kỳ 14 bằng phương pháp Wilder's smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Tính chỉ báo Average True Range (ATR) chu kỳ 14."""
    high = df["High"]
    low = df["Low"]
    close_prev = df["Close"].shift(1)

    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(com=period - 1, min_periods=period).mean()
    return atr


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Tính toán Volume Weighted Average Price (VWAP).
    Sử dụng Typical Price = (High + Low + Close) / 3 nhân với Volume.
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, 1.0)
    pv = typical_price * vol

    rolling_window = 96  # 24 giờ
    cum_pv = pv.rolling(window=rolling_window, min_periods=1).sum()
    cum_vol = vol.rolling(window=rolling_window, min_periods=1).sum()
    return cum_pv / cum_vol


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Tính chỉ số Average Directional Index (ADX) chu kỳ 14 theo chuẩn Wilder's smoothing.
    - ADX > 25: Thị trường có xu hướng mạnh (Trending Market).
    - ADX <= 25: Thị trường đi ngang tích lũy (Sideway / Chop).
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # 1. True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # 2. Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    pos_dm = pd.Series(pos_dm, index=df.index)
    neg_dm = pd.Series(neg_dm, index=df.index)

    # 3. Làm mượt bằng Wilder's EMA
    tr_smooth = tr.ewm(com=period - 1, min_periods=period).mean()
    pos_dm_smooth = pos_dm.ewm(com=period - 1, min_periods=period).mean()
    neg_dm_smooth = neg_dm.ewm(com=period - 1, min_periods=period).mean()

    # 4. Chỉ số +DI và -DI
    pos_di = 100.0 * (pos_dm_smooth / tr_smooth.replace(0, np.nan))
    neg_di = 100.0 * (neg_dm_smooth / tr_smooth.replace(0, np.nan))

    # 5. Directional Index (DX) & ADX
    di_sum = (pos_di + neg_di).replace(0, np.nan)
    dx = 100.0 * ((pos_di - neg_di).abs() / di_sum)
    adx = dx.ewm(com=period - 1, min_periods=period).mean()
    return adx.fillna(20.0)


def extract_features(df_input: pd.DataFrame) -> pd.DataFrame:
    """
    Trích xuất và bổ sung đầy đủ bộ đặc trưng kỹ thuật & MTF + ADX cho tập dữ liệu nến 15m.
    Trả về DataFrame sạch sẵn sàng đưa vào môi trường Gymnasium RL.
    """
    df = df_input.copy()

    rename_map = {c: c.capitalize() for c in df.columns}
    df.rename(columns=rename_map, inplace=True)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)

    # 1. RSI (14)
    df["RSI_14"] = calculate_rsi(df["Close"], period=14)

    # 2. BOLLINGER BANDS (20, 2.0)
    df["BB_middle"] = df["Close"].rolling(window=20).mean()
    df["BB_std"] = df["Close"].rolling(window=20).std()
    df["BB_upper"] = df["BB_middle"] + 2.0 * df["BB_std"]
    df["BB_lower"] = df["BB_middle"] - 2.0 * df["BB_std"]
    bb_range = (df["BB_upper"] - df["BB_lower"]).replace(0, np.nan)
    df["BB_pct"] = (df["Close"] - df["BB_lower"]) / bb_range
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / df["BB_middle"]

    # 3. EMA 50 & EMA 200 (Khung thời gian chính 15m)
    df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA_200"] = df["Close"].ewm(span=200, adjust=False).mean()

    # 4. VWAP
    df["VWAP"] = calculate_vwap(df)

    # 5. ATR (14) - Đo lường biến động
    df["ATR_14"] = calculate_atr(df, period=14)

    # 6. BỘ LỌC XU HƯỚNG ADX (14)
    df["ADX_14"] = calculate_adx(df, period=14)

    # 7. ĐA KHUNG THỜI GIAN (MTF)
    ema_1h_span = 800
    df["EMA_1H_200"] = df["Close"].ewm(span=ema_1h_span, adjust=False).mean()

    max_span_4h = min(1600, max(200, len(df) // 2))
    df["EMA_4H_200"] = df["Close"].ewm(span=max_span_4h, adjust=False).mean()

    # 8. CÁC ĐẶC TRƯNG CHUẨN HÓA CHO RL OBSERVATION SPACE
    atr_safe = df["ATR_14"].replace(0, 1.0)
    df["NORM_DIST_EMA50"] = (df["Close"] - df["EMA_50"]) / atr_safe
    df["NORM_DIST_EMA200"] = (df["Close"] - df["EMA_200"]) / atr_safe
    df["NORM_DIST_VWAP"] = (df["Close"] - df["VWAP"]) / atr_safe
    df["NORM_DIST_MTF_1H"] = (df["Close"] - df["EMA_1H_200"]) / atr_safe
    df["NORM_DIST_MTF_4H"] = (df["Close"] - df["EMA_4H_200"]) / atr_safe
    df["NORM_RSI"] = (df["RSI_14"] - 50.0) / 50.0
    df["NORM_ADX"] = (df["ADX_14"] - 25.0) / 25.0  # > 0: Có xu hướng, <= 0: Đi ngang

    # Loại bỏ các hàng có giá trị NaN do tính toán đường trung bình dài hạn
    df_clean = df.dropna().reset_index(drop=True)
    return df_clean


# Danh sách các cột đặc trưng quan sát dùng cho RL Agent (9 đặc trưng thị trường)
FEATURE_COLUMNS = [
    "NORM_RSI",
    "BB_pct",
    "BB_width",
    "NORM_DIST_EMA50",
    "NORM_DIST_EMA200",
    "NORM_DIST_VWAP",
    "NORM_DIST_MTF_1H",
    "NORM_DIST_MTF_4H",
    "NORM_ADX"
]


if __name__ == "__main__":
    import os
    csv_file = "xau_mexc_real_15m.csv"
    if os.path.exists(csv_file):
        df_raw = pd.read_csv(csv_file)
        df_feat = extract_features(df_raw)
        print("Trích xuất đặc trưng F6 thành công!")
        print(f"Kích thước ban đầu: {df_raw.shape} -> Kích thước sau làm sạch: {df_feat.shape}")
        print("\nThống kê ADX_14:")
        print(f"Số nến ADX > 25 (Có xu hướng): {(df_feat['ADX_14'] > 25).sum():,} / {len(df_feat):,}")
        print("\n5 Hàng đặc trưng đầu tiên:")
        print(df_feat[FEATURE_COLUMNS].head())
    else:
        print(f"Không tìm thấy tệp {csv_file}")
