@echo off
rem Stop XiaoZhi daemon (only kills pc_daemon.main python processes)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*pc_daemon.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('stopped PID ' + $_.ProcessId) }"
del /q "D:\ai-xxq\pc_daemon\daemon.lock" 2>nul
echo XiaoZhi daemon stopped.
