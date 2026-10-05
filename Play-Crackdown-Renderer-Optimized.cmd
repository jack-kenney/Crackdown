@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launch-renderer-test.ps1" -Variant optimized %*
