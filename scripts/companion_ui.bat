@echo off
rem XiaoZhi companion chat (GUI window)
title XiaoZhi Companion GUI
chcp 65001 >nul
cd /d D:\ai-xxq
python -m pc_daemon.companion_ui
echo.
echo (companion GUI exited)
pause
