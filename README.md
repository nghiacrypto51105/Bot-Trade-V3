# 🚀 Bot Trade V3 - BingX Futures Quantitative Trading Terminal (Gold XAU)

Hệ thống Giao Dịch Định Lượng Tự Động (AI Algorithmic Trading) chuyên sâu cho cặp **GOLD / USD (`NCCOGOLD2USD-USDT`)** trên sàn **BingX Perpetual Futures**.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com)

---

## 🌟 Tính Năng Nổi Bật (V3 Highlights)

- ⚡ **Zero-Latency WebSocket Stream**: Kết nối trực tiếp vào luồng máy chủ BingX (`wss://open-api-swap.bingx.com/swap-market`), cập nhật giá mili-giây từng tick, loại bỏ hoàn toàn độ trễ HTTP.
- 🎯 **F8 Alpha Pro Quant Engine**:
  - **EMA 300 (1H Trend Filter)**: Xác định xu hướng vĩ mô dài hạn.
  - **Bollinger Bands (20, 2.0)**: Định vị điểm hồi quy cực trị.
  - **RSI (14)**: Lọc động lượng đảo chiều chuẩn xác.
- 🛡️ **Hệ Thống 4 Tầng Quản Trị Rủi Ro & Sinh Mệnh (Permadeath Shield)**:
  1. **Bar-Lock**: Tuyệt đối không mở quá 1 lệnh trên cùng 1 cây nến 15 phút.
  2. **Dynamic Breakeven Lock**: Chốt 50% ở TP1 (+0.55% / +0.80%), tự động dời SL vào vùng lãi dương.
  3. **Daily Circuit Breaker**: Tối đa 2 Stop Loss/ngày $\implies$ Tự động ngắt mở lệnh nghỉ ngơi bảo vệ vốn.
  4. **Permadeath Stop**: Kiểm soát Max Drawdown nghiêm ngặt dưới 10%.
- 💰 **Tối Ưu Biểu Phí BingX**: Taker 0.05% và Maker Limit TP 0.02% (tiết kiệm tới 41.7% chi phí giao dịch so với các sàn khác).
- 🖥️ **Web Terminal Trực Quan**: Tích hợp biểu đồ Canvas thời gian thực, Radar tư duy AI và sổ lệnh sống động.

---

## 🚀 Hướng Dẫn Triển Khai Miễn Phí Lên Render.com (1-Click Deploy)

### Bước 1: Đăng nhập Render
Truy cập **[Render.com](https://render.com)** và đăng nhập bằng tài khoản GitHub của bạn.

### Bước 2: Tạo Web Service mới
1. Bấm vào nút **"New +"** (góc trên bên phải) $\implies$ chọn **"Web Service"**.
2. Tìm và chọn repository: `nghiacrypto51105/Bot-Trade-V3`.
3. Bấm **"Connect"**.

### Bước 3: Cấu hình thông số (Render Settings)
- **Name**: `bot-trade-v3`
- **Region**: Singapore (hoặc bất kỳ vùng nào gần bạn)
- **Branch**: `main`
- **Root Directory**: Để trống
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn app_dashboard:app --host 0.0.0.0 --port $PORT`
- **Instance Type**: Chọn gói **Free** ($0/tháng).

### Bước 4: Hoàn tất & Khởi chạy
Bấm nút **"Create Web Service"**. Render sẽ tự động build và cấp cho bạn một đường link HTTPS trực tiếp (Ví dụ: `https://bot-trade-v3.onrender.com`) để theo dõi bot 24/7 từ điện thoại hoặc máy tính!

---

## 💻 Hướng Dẫn Chạy Cục Bộ (Local Machine)

```bash
# 1. Cài đặt các thư viện cần thiết
pip install -r requirements.txt

# 2. Khởi chạy máy chủ giao dịch
python app_dashboard.py
```
Trình duyệt sẽ tự động mở trang quản trị tại: `http://127.0.0.1:8000`.

---

## 🔒 Bảo Mật API Keys
API Key và Secret Key của BingX được mã hóa và lưu trực tiếp trong file `.env` cục bộ hoặc biến môi trường (Environment Variables) trên Render, tuyệt đối không bị lộ ra bên ngoài.
