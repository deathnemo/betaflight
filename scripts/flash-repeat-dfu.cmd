@echo off
setlocal EnableExtensions
if "%~2"=="" (
    echo Usage: flash-repeat-dfu.cmd CONFIG COMn [OPTIONS]
    echo Example: flash-repeat-dfu.cmd SPEEDYBEEF405V3 3
    echo   Сборка один раз, затем по Enter — прошивка следующего контроллера на том же COM.
    echo   Третий аргумент — опции gcc ^(как в make OPTIONS^), в кавычках при необходимости.
    exit /b 1
)
if "%~3"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash-repeat-dfu.ps1" -Config "%~1" -ComPort "%~2"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash-repeat-dfu.ps1" -Config "%~1" -ComPort "%~2" -Options "%~3"
)
exit /b %ERRORLEVEL%
