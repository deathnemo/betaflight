@echo off
setlocal EnableExtensions
if "%~1"=="" (
    echo Usage: flash-boards.cmd path\to\boards-flash.json
    echo   Список разных плат: скопируйте scripts\boards-flash.example.json, отредактируйте, затем:
    echo   flash-boards.cmd my-boards.json
    exit /b 1
)
if not exist "%~1" (
    echo File not found: %~1
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0betaflight-flash.ps1" -Manifest "%~f1"
exit /b %ERRORLEVEL%
