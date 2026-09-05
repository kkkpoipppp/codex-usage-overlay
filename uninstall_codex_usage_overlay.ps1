$ErrorActionPreference = "Stop"

$installDirectory = Join-Path $env:LOCALAPPDATA "CodexUsageOverlay"
$startupDirectory = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDirectory "Codex Usage Overlay.lnk"

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "pythonw.exe" -and
        $_.CommandLine -like "*codex_usage_overlay.py*"
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
}
if (Test-Path -LiteralPath $installDirectory) {
    Remove-Item -LiteralPath $installDirectory -Recurse -Force
}

Write-Host "Codex Usage Overlay has been removed."
