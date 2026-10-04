@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0tools\launcher.ps1"
exit /b %errorlevel%
