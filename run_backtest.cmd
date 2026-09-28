@echo off
chcp 65001 > nul
echo ==============================================================================
echo    CO MAY THOI GIAN F8 ALPHA PRO: XAU_USDT (VANG MEXC)
echo    Chien Luoc: Dual-Timeframe Trend + Guaranteed Profit-Lock Trailing
echo ==============================================================================
cd /d "%~dp0"
python backtest_engine.py
pause
