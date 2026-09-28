@echo off
chcp 65001 > nul
echo ================================================================
echo    KHỞI ĐỘNG HỆ THỐNG THỰC THỂ SINH TỒN - MEXC FUTURES
echo ================================================================
cd /d "%~dp0"
python main.py
pause
