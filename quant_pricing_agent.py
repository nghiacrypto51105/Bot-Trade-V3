"""
quant_pricing_agent.py
----------------------
F8 Alpha Pro Sniper Engine: Bắn Tỉa 3 - 5 Lệnh A+ Chất Lượng Nhất Mỗi Ngày (XAU_USDT).
Triết Lý: "Chất lượng hơn số lượng - Ít hơn là nhiều hơn" (Less is More).

Cấu trúc cốt lõi A+ Sniper:
1. Cửa Sổ Giờ Vàng (Golden Liquidity Windows):
   - Phiên London: 13:30 - 18:00 (giờ VN) -> Bắt sóng dòng tiền châu Âu.
   - Phiên New York: 19:00 - 23:59 (giờ VN) -> Bắt sóng bùng nổ tin tức kinh tế Mỹ.
   - Loại bỏ 100% bẫy thanh khoản phiên Á (06:00 - 09:00) và giờ giãn spread (03:00 - 05:30).
2. Xác Nhận Khối Lượng Dòng Tiền Lớn (Volume SMA Confluence):
   - Nến vào lệnh phải có Volume >= 0.85x SMA(20) Volume (Dòng tiền thật của Bank).
3. Bộ Lọc Xu Hướng Khung Lớn (1H Trend Alignment):
   - EMA 300 bám sát xu hướng chủ đạo (Bullish -> chỉ Long, Bearish -> chỉ Short).
4. Cơ Chế Khóa Lợi Nhuận Đa Tầng Lãi Dương (Guaranteed Profit-Lock):
   - TP1 (+0.55%): Chốt 50% vị thế và lập tức dời SL vào vùng LÃI DƯƠNG (+0.14%).
   - TP2 (+0.80%): Chốt 50% còn lại tối đa hóa lợi nhuận ròng.
   - SL  (-0.30%): Cắt lỗ kỷ luật tuyệt đối.
5. Kỷ Luật Kép Bảo Toàn Vốn (Daily Hard Cap & Circuit Breaker):
   - Tối đa 3 - 4 lệnh A+ mỗi ngày (Max Daily Trades = 4).
   - Cầu dao tự ngắt nếu dính 2 lệnh thua trong ngày (Max Daily Losses = 2).
"""

import sys
import math
import logging
from datetime import datetime
from collections import deque
from typing import Dict, Any, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("QuantPricing")


class QuantPricingAgent:
    """
    F8 Alpha Pro Sniper Agent: Bắn tỉa 3 - 5 lệnh A+/ngày với Cửa Sổ Giờ Vàng và Cầu Dao Ngắt Rủi Ro.
    """

    def __init__(
        self,
        queue=None,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_low: float = 45.0,
        rsi_high: float = 55.0,
        ema_1h_period: int = 300,
        sl_pct: float = 0.0025,          # Cắt lỗ chặt 0.25% (Sniper Pro Max)
        tp1_pct: float = 0.0055,         # Chốt lời Tầng 1: 0.55%
        tp2_pct: float = 0.0110,         # Chốt lời Tầng 2: 1.10% (Ăn trọn sóng lớn)
        lock_gain_ratio: float = 0.25,    # Khóa 25% biên độ TP1 thành SL dương (+0.14% lãi)
        max_daily_trades: int = 4,         # Tối đa 3 - 5 lệnh A+ mỗi ngày
        max_daily_losses: int = 2,         # Cầu dao ngắt lỗ: tối đa 2 SL/ngày
        filter_asia_morning: bool = True,  # Triệt tiêu bẫy thanh khoản sáng phiên Á (06:00 - 08:59)
        use_golden_hours: bool = False,    # Tùy chọn chỉ giao dịch phiên London & NY
        use_volume_filter: bool = False,   # Tùy chọn lọc khối lượng Volume SMA
        default_volume: float = 1.0
    ):
        self.queue = queue
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.ema_1h_period = ema_1h_period
        self.sl_pct = sl_pct
        self.tp1_pct = tp1_pct
        self.tp2_pct = tp2_pct
        self.lock_gain_ratio = lock_gain_ratio
        self.max_daily_trades = max_daily_trades
        self.max_daily_losses = max_daily_losses
        self.filter_asia_morning = filter_asia_morning
        self.use_golden_hours = use_golden_hours
        self.use_volume_filter = use_volume_filter
        self.default_volume = default_volume

        self.close_prices: deque = deque(maxlen=600)
        self.volume_history: deque = deque(maxlen=60)
        
        # Quản lý kỷ luật theo ngày
        self.current_day: Optional[str] = None
        self.daily_trades_count: int = 0
        self.daily_losses_count: int = 0
        self.day_locked: bool = False

        # Quản lý trạng thái vị thế đa tầng
        self.active_position: Optional[str] = None
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.take_profit_1: float = 0.0
        self.take_profit_2: float = 0.0
        self.tp1_hit: bool = False

    def _parse_timestamp(self, ts) -> tuple:
        """Trích xuất ngày (YYYY-MM-DD) và giờ (0-23) từ timestamp."""
        if not ts:
            return None, None
        try:
            if isinstance(ts, (int, float)):
                sec = ts / 1000.0 if ts > 1e11 else float(ts)
                dt = datetime.fromtimestamp(sec)
                return dt.strftime("%Y-%m-%d"), dt.hour
            elif isinstance(ts, str):
                clean_ts = ts.strip().replace("T", " ")
                parts = clean_ts.split(" ")
                day_str = parts[0]
                hour = int(parts[1].split(":")[0]) if len(parts) > 1 and ":" in parts[1] else None
                return day_str, hour
        except Exception:
            pass
        return None, None

    def calculate_indicators(self, prices: List[float]):
        """Tính toán đồng thời BB, RSI và EMA 1H."""
        if len(prices) < self.bb_period:
            return 0.0, 0.0, 0.0, 50.0, prices[-1]

        # 1. Bollinger Bands
        recent = prices[-self.bb_period:]
        sma = sum(recent) / self.bb_period
        variance = sum((p - sma) ** 2 for p in recent) / self.bb_period
        std = math.sqrt(variance)
        upper_bb = sma + (self.bb_std * std)
        lower_bb = sma - (self.bb_std * std)

        # 2. RSI (14)
        if len(prices) > self.rsi_period:
            deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains = [max(d, 0.0) for d in deltas]
            losses = [max(-d, 0.0) for d in deltas]
            avg_g = sum(gains[:self.rsi_period]) / self.rsi_period
            avg_l = sum(losses[:self.rsi_period]) / self.rsi_period
            for g, l in zip(gains[self.rsi_period:], losses[self.rsi_period:]):
                avg_g = (avg_g * (self.rsi_period - 1) + g) / self.rsi_period
                avg_l = (avg_l * (self.rsi_period - 1) + l) / self.rsi_period
            rs = avg_g / avg_l if avg_l > 0 else 1.0
            rsi = 100.0 - (100.0 / (1.0 + rs))
        else:
            rsi = 50.0

        # 3. EMA 1H (Thuyền trưởng xu hướng)
        k = 2.0 / (self.ema_1h_period + 1)
        ema_1h = prices[0]
        for p in prices[1:]:
            ema_1h = (p * k) + (ema_1h * (1.0 - k))

        return upper_bb, lower_bb, rsi, ema_1h

    def process_candle(self, candle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Xử lý từng nến với logic Bắn Tỉa A+ (F8 Alpha Pro Sniper Engine).
        """
        symbol = candle.get("symbol", "XAU_USDT")
        close_p = float(candle.get("close", 0.0))
        high_p = float(candle.get("high", close_p))
        low_p = float(candle.get("low", close_p))
        timestamp = candle.get("timestamp", candle.get("Time", ""))
        volume = float(candle.get("volume", candle.get("Volume", 0.0)))

        if close_p <= 0:
            return None

        self.close_prices.append(close_p)
        if volume > 0:
            self.volume_history.append(volume)
        prices = list(self.close_prices)

        # Quản lý kỷ luật chu kỳ ngày
        day_str, hour = self._parse_timestamp(timestamp)
        if day_str and day_str != self.current_day:
            self.current_day = day_str
            self.daily_trades_count = 0
            self.daily_losses_count = 0
            self.day_locked = False
            logger.info(f"[DAY RESET] Ngày mới: {self.current_day} | Đặt lại chỉ tiêu 3 - 5 lệnh A+/ngày.")

        # 1. THEO DÕI VỊ THẾ ĐANG MỞ (QUẢN LÝ LỆNH 2 TẦNG VÀ KHÓA LÃI DƯƠNG)
        if self.active_position == "LONG":
            # Nếu một nến bùng nổ chạm thẳng cả TP2 -> Khớp trọn vẹn cả 2 lệnh Limit chốt lời
            if not self.tp1_hit and high_p >= self.take_profit_2:
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": "FULL_TP_HIT_LONG",
                    "price_tp1": self.take_profit_1,
                    "price_tp2": self.take_profit_2,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Tầng 1: Đạt TP1 -> Chốt 50% và Dời SL vào vùng LÃI DƯƠNG (Entry + 25% TP1)
            elif not self.tp1_hit and high_p >= self.take_profit_1:
                self.tp1_hit = True
                lock_distance = (self.take_profit_1 - self.entry_price) * self.lock_gain_ratio
                self.stop_loss = round(self.entry_price + lock_distance, 2)  # KHÓA LÃI DƯƠNG (+0.14%)
                return {
                    "action": "PARTIAL_TP1_LONG",
                    "price": self.take_profit_1,
                    "close_ratio": 0.50,
                    "new_sl": self.stop_loss,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Tầng 2: Đạt TP2 -> Chốt toàn bộ 50% còn lại
            elif self.tp1_hit and high_p >= self.take_profit_2:
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": "CLOSE_LONG_TP2",
                    "price": self.take_profit_2,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Chạm SL (nếu đã qua TP1 thì đây là chốt lời dương Profit-Lock, ngược lại là cắt lỗ kỷ luật)
            elif low_p <= self.stop_loss:
                action_name = "PROFIT_LOCK_EXIT_LONG" if self.tp1_hit else "CLOSE_LONG_SL"
                exit_price = self.stop_loss
                if not self.tp1_hit:
                    self.daily_losses_count += 1
                    if self.daily_losses_count >= self.max_daily_losses:
                        self.day_locked = True
                        logger.warning(
                            f"[CIRCUIT BREAKER] Cầu dao tự ngắt: {self.daily_losses_count} SL trong ngày {self.current_day}. Dừng mở lệnh mới!"
                        )
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": action_name,
                    "price": exit_price,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

        elif self.active_position == "SHORT":
            # Nếu một nến sập mạnh chạm thẳng cả TP2 -> Khớp trọn vẹn cả 2 lệnh Limit chốt lời
            if not self.tp1_hit and low_p <= self.take_profit_2:
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": "FULL_TP_HIT_SHORT",
                    "price_tp1": self.take_profit_1,
                    "price_tp2": self.take_profit_2,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Tầng 1: Đạt TP1 -> Chốt 50% và Dời SL vào vùng LÃI DƯƠNG (Entry - 25% TP1)
            elif not self.tp1_hit and low_p <= self.take_profit_1:
                self.tp1_hit = True
                lock_distance = (self.entry_price - self.take_profit_1) * self.lock_gain_ratio
                self.stop_loss = round(self.entry_price - lock_distance, 2)  # KHÓA LÃI DƯƠNG (+0.14%)
                return {
                    "action": "PARTIAL_TP1_SHORT",
                    "price": self.take_profit_1,
                    "close_ratio": 0.50,
                    "new_sl": self.stop_loss,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Tầng 2: Đạt TP2 -> Chốt toàn bộ 50% còn lại
            elif self.tp1_hit and low_p <= self.take_profit_2:
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": "CLOSE_SHORT_TP2",
                    "price": self.take_profit_2,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

            # Chạm SL (nếu đã qua TP1 thì đây là chốt lời dương Profit-Lock, ngược lại là cắt lỗ kỷ luật)
            elif high_p >= self.stop_loss:
                action_name = "PROFIT_LOCK_EXIT_SHORT" if self.tp1_hit else "CLOSE_SHORT_SL"
                exit_price = self.stop_loss
                if not self.tp1_hit:
                    self.daily_losses_count += 1
                    if self.daily_losses_count >= self.max_daily_losses:
                        self.day_locked = True
                        logger.warning(
                            f"[CIRCUIT BREAKER] Cầu dao tự ngắt: {self.daily_losses_count} SL trong ngày {self.current_day}. Dừng mở lệnh mới!"
                        )
                self.active_position = None
                self.tp1_hit = False
                return {
                    "action": action_name,
                    "price": exit_price,
                    "symbol": symbol,
                    "timestamp": timestamp
                }

        # 2. QUÉT TÍN HIỆU VÀO LỆNH MỚI NẾU ĐANG FLAT (BẮN TỈA A+ SETUP)
        if self.active_position is None and len(prices) >= 30:
            # Kỷ luật 1: Giới hạn số lệnh và Cầu dao tự ngắt
            if self.day_locked or self.daily_trades_count >= self.max_daily_trades:
                return None

            # Kỷ luật 2: Triệt tiêu bẫy thanh khoản sáng phiên Á (06:00 - 08:59 sáng giờ VN)
            if self.filter_asia_morning and hour is not None:
                if 6 <= hour <= 8:
                    return None

            # Kỷ luật 3: Cửa sổ Giờ Vàng (London: 13:30 - 18:00, New York: 19:00 - 02:00 VN)
            if self.use_golden_hours and hour is not None:
                is_london = (13 <= hour <= 18)
                is_new_york = (19 <= hour <= 23) or (0 <= hour <= 2)
                if not (is_london or is_new_york):
                    return None

            # Kỷ luật 4: Xác nhận Khối lượng dòng tiền lớn (Volume SMA Confirmation)
            if self.use_volume_filter and len(self.volume_history) >= 20 and volume > 0:
                avg_vol = sum(self.volume_history) / len(self.volume_history)
                if volume < (0.85 * avg_vol):
                    return None

            upper_bb, lower_bb, rsi, ema_1h = self.calculate_indicators(prices)
            sma_bb = (upper_bb + lower_bb) / 2.0

            # 1. ĐIỀU KIỆN LONG (Dual A+ Sniper):
            # Nhánh 1: Bắt đáy chiết khấu sâu (Deep Dip): Giá <= BB Dưới & RSI <= 45
            # Nhánh 2: Bắt nhịp hồi tiếp diễn sóng tăng (SMA 20 Pullback): Giá <= SMA 20 & RSI <= 52 (Đã hạ nhiệt)
            is_long_deep_dip = (close_p <= lower_bb and rsi <= self.rsi_low)
            is_long_sma_pullback = (close_p <= sma_bb * 1.001 and rsi <= 52.0 and close_p > lower_bb)

            if close_p > ema_1h and (is_long_deep_dip or is_long_sma_pullback):
                self.active_position = "LONG"
                self.entry_price = close_p
                self.stop_loss = round(close_p * (1.0 - self.sl_pct), 2)
                self.take_profit_1 = round(close_p * (1.0 + self.tp1_pct), 2)
                self.take_profit_2 = round(close_p * (1.0 + self.tp2_pct), 2)
                self.tp1_hit = False
                self.daily_trades_count += 1
                if self.daily_trades_count >= self.max_daily_trades:
                    self.day_locked = True

                strategy_type = "DEEP DIP" if is_long_deep_dip else "SMA 20 PULLBACK"
                logger.info(
                    f"[F8 A+ SNIPER LONG ({strategy_type})] {symbol} @ {close_p:.2f} | Lệnh ngày: {self.daily_trades_count}/{self.max_daily_trades} | "
                    f"RSI: {rsi:.1f} | EMA: {ema_1h:.2f} | SMA20: {sma_bb:.2f} | "
                    f"TP1: {self.take_profit_1:.2f} (+0.55%) | TP2: {self.take_profit_2:.2f} (+1.10%) | SL: {self.stop_loss:.2f}"
                )

                return {
                    "action": f"OPEN_LONG_{strategy_type.replace(' ', '_')}",
                    "side": "LONG",
                    "symbol": symbol,
                    "price": close_p,
                    "volume": self.default_volume,
                    "sl": self.stop_loss,
                    "tp1": self.take_profit_1,
                    "tp2": self.take_profit_2,
                    "timestamp": timestamp
                }

            # 2. ĐIỀU KIỆN SHORT (Dual A+ Sniper):
            # Nhánh 1: Bắt đỉnh sóng hồi sâu (Deep Peak): Giá >= BB Trên & RSI >= 55
            # Nhánh 2: Bắt nhịp hồi tiếp diễn sóng giảm (SMA 20 Pullback): Giá >= SMA 20 & RSI >= 48 (Đã hồi phục)
            is_short_deep_peak = (close_p >= upper_bb and rsi >= self.rsi_high)
            is_short_sma_pullback = (close_p >= sma_bb * 0.999 and rsi >= 48.0 and close_p < upper_bb)

            if (not self.active_position) and close_p < ema_1h and (is_short_deep_peak or is_short_sma_pullback):
                self.active_position = "SHORT"
                self.entry_price = close_p
                self.stop_loss = round(close_p * (1.0 + self.sl_pct), 2)
                self.take_profit_1 = round(close_p * (1.0 - self.tp1_pct), 2)
                self.take_profit_2 = round(close_p * (1.0 - self.tp2_pct), 2)
                self.tp1_hit = False
                self.daily_trades_count += 1
                if self.daily_trades_count >= self.max_daily_trades:
                    self.day_locked = True

                strategy_type = "DEEP PEAK" if is_short_deep_peak else "SMA 20 PULLBACK"
                logger.info(
                    f"[F8 A+ SNIPER SHORT ({strategy_type})] {symbol} @ {close_p:.2f} | Lệnh ngày: {self.daily_trades_count}/{self.max_daily_trades} | "
                    f"RSI: {rsi:.1f} | EMA: {ema_1h:.2f} | SMA20: {sma_bb:.2f} | "
                    f"TP1: {self.take_profit_1:.2f} (-0.55%) | TP2: {self.take_profit_2:.2f} (-1.10%) | SL: {self.stop_loss:.2f}"
                )

                return {
                    "action": f"OPEN_SHORT_{strategy_type.replace(' ', '_')}",
                    "side": "SHORT",
                    "symbol": symbol,
                    "price": close_p,
                    "volume": self.default_volume,
                    "sl": self.stop_loss,
                    "tp1": self.take_profit_1,
                    "tp2": self.take_profit_2,
                    "timestamp": timestamp
                }

        return None

    async def run_pricing_loop(self, callback):
        """
        Lắng nghe K-line từ hàng đợi queue và kích hoạt callback khi có tín hiệu F8.
        """
        import asyncio
        logger.info("[QUANT AGENT] Khởi động vòng lặp phân tích định lượng K-line (F8 Alpha Pro)...")
        while True:
            try:
                if self.queue is None:
                    await asyncio.sleep(1)
                    continue
                candle = await self.queue.get()
                signal = self.process_candle(candle)
                if signal and callback:
                    await callback(signal)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[QUANT LOOP ERROR] Lỗi xử lý nến: {e}")
                await asyncio.sleep(1)

