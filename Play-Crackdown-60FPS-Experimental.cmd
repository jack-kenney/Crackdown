@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0tools\launch-native-timing.ps1"
exit /b %errorlevel%
