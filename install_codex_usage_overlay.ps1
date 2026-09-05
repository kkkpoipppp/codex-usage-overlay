$ErrorActionPreference = "Stop"

$sourceDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDirectory = Join-Path $env:LOCALAPPDATA "CodexUsageOverlay"
$startupDirectory = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDirectory "Codex Usage Overlay.lnk"
$installedScript = Join-Path $installDirectory "codex_usage_overlay.py"

$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source

$overlayProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.Name -eq "pythonw.exe" -and
        $_.CommandLine -like "*codex_usage_overlay.py*"
    }

$overlayProcessIds = @($overlayProcesses | ForEach-Object { $_.ProcessId })
if ($overlayProcessIds.Count -gt 0) {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ParentProcessId -in $overlayProcessIds -and
            $_.Name -eq "codex.exe" -and
            $_.CommandLine -like "*app-server*stdio://*"
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    $overlayProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceDirectory "codex_usage_overlay.py") -Destination $installedScript -Force

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $installedScript + '"'
$shortcut.WorkingDirectory = $installDirectory
$shortcut.Description = "Show Codex five-hour and weekly usage above the username"
$shortcut.Save()

Start-Process -FilePath $pythonw -ArgumentList ('"' + $installedScript + '"') -WindowStyle Hidden

Write-Host "Installed: $installDirectory"
Write-Host "Startup shortcut: $shortcutPath"
