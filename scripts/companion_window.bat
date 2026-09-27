@echo off
rem XiaoZhi companion chat window
title XiaoZhi Companion
chcp 65001 >nul
cd /d D:\ai-xxq
python -m pc_daemon.companion
echo.
echo (companion exited)
pause
