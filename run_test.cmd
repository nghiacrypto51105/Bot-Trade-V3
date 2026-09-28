@echo off
chcp 65001 > nul
echo ================================================================
echo    CHẠY KIỂM THỬ HỆ THỐNG AI AGENT GIAO DỊCH MEXC FUTURES
echo ================================================================
cd /d "%~dp0"

echo [1/2] Đang cài đặt thư viện cần thiết...
pip install requests websockets python-dotenv --quiet

echo [2/2] Bắt đầu chạy kịch bản kiểm thử Lõi Sinh Tồn & Permadeath...
python test_survival_system.py

pause
