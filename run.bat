@echo off
rem Lance eCasa depuis le dossier du projet (Windows).
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m ecasa.main
) else (
    python -m ecasa.main
)

if errorlevel 1 pause
