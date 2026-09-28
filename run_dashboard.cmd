@echo off
chcp 65001 > nul
title MEXC AI Trading Terminal Pro - XAU/USDT Dashboard
echo ========================================================
echo   KHOI DONG WEB DASHBOARD DIEU KHIEN AI TRADING MEXC
echo   Che do: Demo / Paper Trading (Du lieu nen that MEXC)
echo ========================================================
echo.
echo Dang mo may chu tai http://127.0.0.1:8000 ...
echo Trinh duyet se tu dong mo len trong giay lat!
echo.
python app_dashboard.py
pause
