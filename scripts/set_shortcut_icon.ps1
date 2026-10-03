# set_shortcut_icon.ps1 —— 云小小桌面快捷方式换柑橘图标
$ws = New-Object -ComObject WScript.Shell
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) '云小小.lnk'
$lnk = $ws.CreateShortcut($path)
Write-Output ("Target: " + $lnk.TargetPath)
$lnk.IconLocation = 'D:\ai-xxq\pc_daemon\assets\yunxxq.ico,0'
$lnk.Save()
$check = $ws.CreateShortcut($path)
Write-Output ("Icon now: " + $check.IconLocation)
