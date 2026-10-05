@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launch-physics-trace.ps1" -NormalizeCars %*
