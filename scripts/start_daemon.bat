@echo off
rem Start XiaoZhi daemon (hidden via VBS). Output goes to startup.log for debugging.
cd /d D:\ai-xxq
echo ===== %date% %time% starting ===== >> "D:\ai-xxq\pc_daemon\logs\startup.log"
"D:\python\pythonapp\pythonw.exe" -m pc_daemon.main 1>>"D:\ai-xxq\pc_daemon\logs\startup.log" 2>&1
echo ===== %date% %time% exited with %errorlevel% ===== >> "D:\ai-xxq\pc_daemon\logs\startup.log"
