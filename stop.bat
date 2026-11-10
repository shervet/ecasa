@echo off
rem Arrete MeCasa et les services lies (Windows).
cd /d "%~dp0"

echo Arret de MeCasa et des services lies...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"

echo.
echo Arret termine.
pause