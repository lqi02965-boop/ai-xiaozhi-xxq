# check_crash.ps1 - look for pythonw crash events in last 3 hours
$ev = Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1002; StartTime=(Get-Date).AddHours(-3)} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -like '*python*' } |
    Select-Object -First 5 TimeCreated, Id, @{n='Msg';e={$_.Message.Substring(0, [Math]::Min(300, $_.Message.Length))}}
if ($ev) { $ev | Format-List } else { Write-Output "no python crash events in last 3h" }
