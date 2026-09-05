@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %ERRORLEVEL%==0 (
  start "" pythonw.exe "%~dp0codex_usage_overlay.py"
) else (
  start "" python.exe "%~dp0codex_usage_overlay.py"
)
