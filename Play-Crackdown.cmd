@echo off
setlocal
pushd "%~dp0"
if not exist "out\build\win-amd64-release\crackdown.exe" (
    echo Crackdown has not been built. Run build-local.ps1 first.
    pause
    popd
    exit /b 1
)
"out\build\win-amd64-release\crackdown.exe" --game_data_root="%~dp0assets" --user_data_root="%~dp0out\userdata" --log_file="%~dp0out\crackdown.log" --enable_console=false --audio_maxqframes=8
set "gameExit=%errorlevel%"
if not "%gameExit%"=="0" (
    echo Crackdown exited with code %gameExit%. See out\crackdown.log.
    pause
)
popd
exit /b %gameExit%
