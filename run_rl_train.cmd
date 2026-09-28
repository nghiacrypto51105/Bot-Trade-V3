@echo off
chcp 65001 > nul
echo ==============================================================================
echo    HUAN LUYEN BO NAO HOC TANG CUONG F6 (PPO + BO LOC XU HUONG ADX)
echo    Phi MEXC: 0.02%% ^| Bo loc ADX ^> 25 ^| Timesteps: 300,000 steps
echo ==============================================================================
cd /d "%~dp0"
python rl_brain_agent.py --train --backtest --timesteps 300000
pause
