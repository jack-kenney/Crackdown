@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0tools\launch-performance-test.ps1" %*
exit /b %errorlevel%
