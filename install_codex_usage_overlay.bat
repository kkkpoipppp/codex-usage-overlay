@echo off
setlocal
where pwsh.exe >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_codex_usage_overlay.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_codex_usage_overlay.ps1"
)
if errorlevel 1 pause
